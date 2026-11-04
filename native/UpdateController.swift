import AppKit
import Sparkle

@MainActor
final class UpdateController: NSObject, SPUUpdaterDelegate {
    private var controller: SPUStandardUpdaterController!
    private var checkObservation: NSKeyValueObservation?
    private var automaticObservation: NSKeyValueObservation?
    private let busyChecker: (@escaping (Bool) -> Void) -> Void
    private let changed: () -> Void
    private var installHandler: (() -> Void)?
    private var startupError: String?
    private(set) var available = false

    init(busyChecker: @escaping (@escaping (Bool) -> Void) -> Void, changed: @escaping () -> Void) {
        self.busyChecker = busyChecker
        self.changed = changed
        super.init()
        controller = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        do {
            try controller.updater.start()
            available = true
        } catch {
            startupError = "Updates unavailable: \(error.localizedDescription)"
        }
        checkObservation = controller.updater.observe(\.canCheckForUpdates, options: [.new]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.changed() }
        }
        automaticObservation = controller.updater.observe(\.automaticallyChecksForUpdates, options: [.new]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.changed() }
        }
    }

    var canCheckForUpdates: Bool { available && controller.updater.canCheckForUpdates }
    var automaticUpdateChecks: Bool { available && controller.updater.automaticallyChecksForUpdates }
    var message: String {
        if let startupError { return startupError }
        if installHandler != nil { return "Waiting for local operations to finish before installing. Keep Worktree Monitor open." }
        if !canCheckForUpdates { return "An update check or installation is in progress." }
        return automaticUpdateChecks ? "Automatic checks are on. Installation still requires your approval." : "Check manually, or turn on automatic checks."
    }

    func checkForUpdates() {
        guard canCheckForUpdates else { return }
        controller.checkForUpdates(nil)
        changed()
    }

    func setAutomaticUpdateChecks(_ enabled: Bool) {
        guard available else { return }
        controller.updater.automaticallyChecksForUpdates = enabled
        changed()
    }

    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem, untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        self.installHandler = installHandler
        changed()
        // Always return first; the installer continuation may synchronously terminate the app.
        DispatchQueue.main.async { [weak self] in self?.waitForIdle() }
        return true
    }

    private func waitForIdle() {
        guard installHandler != nil else { return }
        busyChecker { [weak self] busy in
            DispatchQueue.main.async {
                guard let self, self.installHandler != nil else { return }
                if busy {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in self?.waitForIdle() }
                } else {
                    let install = self.installHandler
                    self.installHandler = nil
                    install?()
                }
            }
        }
    }

    func stop() {
        checkObservation?.invalidate()
        automaticObservation?.invalidate()
        installHandler = nil
    }
}
