import AppKit
import CoreFoundation
import CoreImage
import ServiceManagement
import WebKit

private func appearanceName(for mode: String) -> NSAppearance.Name? {
    switch mode {
    case "light": return .aqua
    case "dark": return .darkAqua
    default: return nil
    }
}

private func dashboardURL(from line: String) -> URL? {
    let prefix = "Workspace dashboard: "
    guard line.hasPrefix(prefix),
          let url = URL(string: String(line.dropFirst(prefix.count))),
          url.scheme == "http", url.host == "127.0.0.1",
          let port = url.port, (1...65535).contains(port),
          (url.path.isEmpty || url.path == "/"), url.query == nil, url.fragment == nil else { return nil }
    return url
}

// Match the dashboard's semantic colors in both appearances.
private enum PanelPalette {
    static func color(_ light: UInt32, _ dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let value = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: CGFloat((value >> 16) & 255) / 255, green: CGFloat((value >> 8) & 255) / 255, blue: CGFloat(value & 255) / 255, alpha: 1)
        }
    }
    static let paper = color(0xf4f1eb, 0x141d1a)
    static let ink = color(0x193f36, 0xdfebe4)
    static let muted = color(0x65736d, 0xa2b4aa)
    static let line = color(0xd8ddd6, 0x3b4d43)
    static let coral = color(0xdb705f, 0xed9582)
    static let mint = color(0xc9e1d2, 0x537963)
    static let surface = color(0xffffff, 0x1e2a24)
    static let dots = color(0xc8cdc5, 0x2a3b31)
    static let attention = color(0xf9e4df, 0x4a302c)
    static let attentionInk = color(0x783c32, 0xffc1b2)
    static let error = color(0xa54439, 0xffab99)
}

private final class HerdrStatusMark: NSImageView {
    let workingArc = CAShapeLayer()
    private var state = "unknown"
    private var motionObserver: NSObjectProtocol?

    init(state: String) {
        super.init(frame: .zero)
        wantsLayer = true
        workingArc.fillColor = nil
        workingArc.lineCap = .round
        layer?.addSublayer(workingArc)
        imageScaling = .scaleProportionallyUpOrDown
        setAccessibilityHidden(true)
        motionObserver = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification, object: nil, queue: .main) { [weak self] _ in self?.updateMotion() }
        setState(state)
    }
    required init?(coder: NSCoder) { return nil }
    deinit { if let motionObserver { NSWorkspace.shared.notificationCenter.removeObserver(motionObserver) } }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); updateMotion() }
    override func layout() {
        super.layout()
        // Rotate a separate layer around its center; AppKit owns the view layer's anchor.
        CATransaction.begin(); CATransaction.setDisableActions(true)
        workingArc.bounds = NSRect(origin: .zero, size: bounds.size)
        workingArc.position = NSPoint(x: bounds.midX, y: bounds.midY)
        let size = min(bounds.width, bounds.height)
        let path = CGMutablePath()
        path.addArc(center: NSPoint(x: size / 2, y: size / 2), radius: size * 0.375, startAngle: 0, endAngle: 1.5 * .pi, clockwise: false)
        workingArc.path = path; workingArc.lineWidth = size * 2.6 / 24
        CATransaction.commit()
    }
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance(); updateArcColor()
    }
    private func updateArcColor() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            workingArc.strokeColor = contentTintColor?.cgColor
        }
    }

    func setState(_ state: String) {
        self.state = state
        switch state {
        case "working": contentTintColor = PanelPalette.color(0x95600b, 0xffe1a3)
        case "blocked": contentTintColor = PanelPalette.color(0xb92d60, 0xfa88ab)
        case "ready", "clear": contentTintColor = PanelPalette.color(0x177c70, 0x8ddfd1)
        case "idle": contentTintColor = PanelPalette.color(0x397d3e, 0x9cde9d)
        default: contentTintColor = PanelPalette.color(0x707799, 0xa2a7ca)
        }
        workingArc.isHidden = state != "working"
        updateArcColor()
        if state == "working" {
            image = nil
        } else if state == "unknown" {
            image = NSImage(size: NSSize(width: 24, height: 24), flipped: false) { _ in
                NSColor.black.setFill()
                NSBezierPath(ovalIn: NSRect(x: 8.6, y: 8.6, width: 6.8, height: 6.8)).fill()
                return true
            }
        } else {
            let name = ["blocked": "xmark.circle", "ready": "checkmark.circle", "clear": "checkmark.circle", "idle": "moon", "offline": "questionmark.circle"][state] ?? "circle.fill"
            image = NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(.init(pointSize: 20, weight: .semibold))
        }
        image?.isTemplate = true
        updateMotion()
    }
    private func updateMotion() {
        workingArc.removeAnimation(forKey: "working")
        guard state == "working", window != nil, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        let rotation = CABasicAnimation(keyPath: "transform.rotation.z")
        rotation.fromValue = 0; rotation.toValue = -2 * Double.pi; rotation.duration = 1.1; rotation.repeatCount = .infinity
        workingArc.add(rotation, forKey: "working")
    }
}

private final class HerdrPanelBackground: NSView {
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); needsDisplay = true }
    override func draw(_ dirtyRect: NSRect) {
        PanelPalette.paper.setFill(); dirtyRect.fill()
        PanelPalette.dots.setFill()
        for x in stride(from: CGFloat(8), to: bounds.width, by: 17) {
            for y in stride(from: CGFloat(8), to: bounds.height, by: 17) {
                NSBezierPath(ovalIn: NSRect(x: x, y: y, width: 1.4, height: 1.4)).fill()
            }
        }
    }
}

private final class HerdrChatCell: NSButtonCell {
    override func imageRect(forBounds bounds: NSRect) -> NSRect {
        NSRect(x: bounds.minX + 10, y: bounds.midY - 12, width: 24, height: 24)
    }
    override func titleRect(forBounds bounds: NSRect) -> NSRect {
        NSRect(x: bounds.minX + 44, y: bounds.midY - 18, width: max(0, bounds.width - 98), height: 36)
    }
    override func drawTitle(_ title: NSAttributedString, withFrame frame: NSRect, in controlView: NSView) -> NSRect {
        super.drawTitle(title, withFrame: titleRect(forBounds: controlView.bounds), in: controlView)
    }
}

private final class HerdrChatButton: NSButton {
    private var statusMark: HerdrStatusMark?
    func setStatus(_ state: String) {
        let mark = HerdrStatusMark(state: state)
        statusMark?.removeFromSuperview(); statusMark = mark
        image = NSImage(size: NSSize(width: 24, height: 24), flipped: false) { _ in true }
        imagePosition = .imageLeading
        addSubview(mark)
        needsLayout = true
    }
    override func layout() {
        super.layout()
        statusMark?.frame = cell?.imageRect(forBounds: bounds) ?? .zero
    }
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); needsDisplay = true }
    override func highlight(_ flag: Bool) { super.highlight(flag); needsDisplay = true }
    override func draw(_ dirtyRect: NSRect) {
        let card = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: 8, yRadius: 8)
        (cell?.isHighlighted == true ? PanelPalette.mint : PanelPalette.surface).setFill(); card.fill()
        PanelPalette.line.setStroke(); card.lineWidth = 1; card.stroke()
        NSGraphicsContext.saveGraphicsState()
        card.addClip()
        (statusMark?.contentTintColor ?? PanelPalette.muted).setFill()
        NSRect(x: 0, y: isFlipped ? 0 : bounds.height - 3, width: bounds.width, height: 3).fill()
        NSGraphicsContext.restoreGraphicsState()
        super.draw(dirtyRect)
    }
}

private enum PanelMode: String, CaseIterable, Codable {
    case attention, unread, ready, input, all, pinned
    var title: String {
        switch self {
        case .attention: return "Needs attention"
        case .unread: return "Unread replies"
        case .ready: return "Ready for reply"
        case .input: return "Needs input"
        case .all: return "All chats"
        case .pinned: return "Pinned chats"
        }
    }
}

private struct HerdrStatus: Decodable, Equatable {
    struct Chat: Decodable, Equatable {
        let id, name, workspace, agent, state: String
        var unread: Bool? = nil
        var pinned: Bool? = nil
    }
    let status, message, csrf: String
    var chats: [Chat]
    var allChats: [Chat]? = nil
    var mode: PanelMode? = nil
    enum CodingKeys: String, CodingKey { case status, message, csrf, chats, mode; case allChats = "all_chats" }
    func filtered(for mode: PanelMode) -> HerdrStatus {
        var result = self
        result.mode = mode
        result.chats = (allChats ?? chats).filter { chat in
            switch mode {
            case .attention: return ["blocked", "ready"].contains(chat.state)
            case .unread: return chat.unread == true
            case .ready: return chat.state == "ready"
            case .input: return chat.state == "blocked"
            case .all: return true
            case .pinned: return chat.pinned == true
            }
        }
        let order = ["blocked": 0, "ready": 1, "working": 2, "idle": 3]
        result.chats = result.chats.enumerated().sorted { a, b in
            let aPinned = a.element.pinned == true, bPinned = b.element.pinned == true
            if aPinned != bPinned { return aPinned }
            if mode == .all || mode == .pinned {
                let aRank = order[a.element.state] ?? 4, bRank = order[b.element.state] ?? 4
                if aRank != bRank { return aRank < bRank }
            }
            return a.offset < b.offset
        }.map { $0.element }
        return result
    }
    static let unavailable = HerdrStatus(status: "unavailable", message: "Herdr is unavailable. Open Herdr to reconnect.", csrf: "", chats: [])
    var available: Bool { status == "available" }
    var needsInput: Bool { chats.contains { $0.state == "blocked" } }
    func hasNewAttention(comparedTo previous: HerdrStatus) -> Bool {
        let seen = Set(previous.chats.map { "\($0.id):\($0.state)" })
        return available && chats.contains { ["blocked", "ready"].contains($0.state) && !seen.contains("\($0.id):\($0.state)") }
    }
    var eyebrow: String { !available ? "HERDR OFFLINE" : needsInput ? "YOUR TURN" : chats.isEmpty ? "ALL CLEAR" : mode == .unread ? "UNREAD REPLIES" : mode == .all ? "ALL CHATS" : mode == .pinned ? "PINNED CHATS" : "READY FOR YOU" }
    var indicatorState: String {
        if !available { return "offline" }
        if needsInput { return "blocked" }
        if chats.isEmpty { return "clear" }
        if chats.contains(where: { $0.state == "ready" }) { return "ready" }
        if chats.contains(where: { $0.state == "working" }) { return "working" }
        return chats.allSatisfy { $0.state == "idle" } ? "idle" : "unknown"
    }
    var menuTitle: String { available ? (chats.isEmpty ? "" : " \(chats.count)") : " ?" }
    var heading: String {
        if !available { return "Herdr unavailable" }
        if chats.isEmpty {
            switch mode ?? .attention {
            case .attention: return "All caught up"
            case .unread: return "No unread replies."
            case .ready: return "No replies waiting."
            case .input: return "No input needed."
            case .all: return "No chats here."
            case .pinned: return "No pinned chats."
            }
        }
        return needsInput ? "Waiting on you." : mode == .all ? "Your chats, in view." : mode == .pinned ? "Your pinned chats." : mode == .unread ? "Unread replies." : "Replies are ready."
    }
    var detail: String {
        if !available { return message }
        let waiting = chats.filter { $0.state == "blocked" }.count
        switch mode ?? .attention {
        case .unread: return "Completed replies not yet marked viewed by Herdr."
        case .ready: return "Chats Herdr currently marks as completed."
        case .input: return "Questions and approvals that need your response."
        case .pinned: return "Chats you pinned on your Mac or phone."
        case .all: return "\(chats.count) chats · \(chats.filter { $0.state == "working" }.count) working · \(waiting) need input"
        case .attention: return chats.isEmpty ? "No questions or completed chats waiting." : "\(waiting) need input · \(chats.count - waiting) ready for reply"
        }
    }
}

if CommandLine.arguments.contains("--self-check") {
    let spinner = HerdrStatusMark(state: "working")
    for size: CGFloat in [20, 24] {
        spinner.frame = NSRect(x: 10, y: 16, width: size, height: size)
        spinner.layout()
        precondition(spinner.workingArc.anchorPoint == NSPoint(x: 0.5, y: 0.5))
        precondition(spinner.workingArc.position == NSPoint(x: size / 2, y: size / 2))
        precondition(spinner.workingArc.bounds.size == spinner.bounds.size)
        let arc = spinner.workingArc.path!.boundingBoxOfPath
        precondition(abs(arc.midX - size / 2) < 0.01 && abs(arc.midY - size / 2) < 0.01)
    }
    let chatCell = HerdrChatCell(textCell: "A long workspace name that must truncate")
    for width: CGFloat in [180, 202, 332] {
        let bounds = NSRect(x: 0, y: 0, width: width, height: 56)
        let icon = chatCell.imageRect(forBounds: bounds), title = chatCell.titleRect(forBounds: bounds)
        precondition(!icon.intersects(title) && title.minX - icon.maxX >= 10)
        precondition(bounds.contains(icon) && bounds.contains(title))
        let pin = NSRect(x: bounds.maxX - 42, y: bounds.midY - 16, width: 32, height: 32)
        precondition(!title.intersects(pin) && pin.minX - title.maxX >= 10)
        precondition(bounds.contains(pin))
    }
    precondition(dashboardURL(from: "Workspace dashboard: http://127.0.0.1:43210")?.port == 43210)
    precondition(dashboardURL(from: "Workspace dashboard: http://127.0.0.1:0") == nil)
    precondition(dashboardURL(from: "Workspace dashboard: https://127.0.0.1:43210") == nil)
    precondition(dashboardURL(from: "Workspace dashboard: http://example.com:43210") == nil)
    precondition(appearanceName(for: "system") == nil)
    precondition(appearanceName(for: "light") == .aqua)
    precondition(appearanceName(for: "dark") == .darkAqua)
    let ready = HerdrStatus(status: "available", message: "", csrf: "test", chats: [])
    precondition(ready.menuTitle.isEmpty && ready.heading == "All caught up")
    precondition(HerdrStatus.unavailable.menuTitle == " ?")
    let waiting = HerdrStatus(status: "available", message: "", csrf: "test", chats: [.init(id: "w1:p1", name: "Chat", workspace: "Test", agent: "codex", state: "blocked")])
    precondition(waiting.menuTitle == " 1" && waiting.detail == "1 need input · 0 ready for reply")
    precondition(waiting.heading == "Waiting on you." && waiting.eyebrow == "YOUR TURN" && waiting.needsInput)
    let completed = HerdrStatus(status: "available", message: "", csrf: "test", chats: [.init(id: "w2:p1", name: "Chat", workspace: "Test", agent: "codex", state: "ready")])
    precondition(completed.heading == "Replies are ready." && completed.eyebrow == "READY FOR YOU" && !completed.needsInput)
    precondition(ready.eyebrow == "ALL CLEAR" && HerdrStatus.unavailable.eyebrow == "HERDR OFFLINE")
    precondition(waiting.hasNewAttention(comparedTo: ready))
    precondition(!waiting.hasNewAttention(comparedTo: waiting))
    precondition(!ready.hasNewAttention(comparedTo: waiting))
    precondition(!HerdrStatus.unavailable.hasNewAttention(comparedTo: waiting))
    precondition(HerdrStatus.unavailable.indicatorState == "offline" && ready.indicatorState == "clear")
    precondition(waiting.indicatorState == "blocked" && completed.indicatorState == "ready")
    for state in ["working", "idle", "unknown"] {
        let status = HerdrStatus(status: "available", message: "", csrf: "test", chats: [.init(id: state, name: "Chat", workspace: "Test", agent: "codex", state: state)])
        precondition(status.indicatorState == state)
    }
    let activity = HerdrStatus(status: "available", message: "", csrf: "test", chats: [], allChats: [
        .init(id: "read", name: "Read", workspace: "Test", agent: "codex", state: "ready", unread: false),
        .init(id: "unread", name: "Unread", workspace: "Test", agent: "codex", state: "ready", unread: true),
        .init(id: "input", name: "Input", workspace: "Test", agent: "codex", state: "blocked", unread: false),
        .init(id: "working", name: "Working", workspace: "Test", agent: "codex", state: "working", unread: false),
        .init(id: "idle", name: "Idle", workspace: "Test", agent: "codex", state: "idle", unread: false)
    ])
    precondition(activity.filtered(for: .unread).chats.map { $0.id } == ["unread"])
    precondition(activity.filtered(for: .ready).chats.map { $0.id } == ["read", "unread"])
    precondition(activity.filtered(for: .input).chats.map { $0.id } == ["input"])
    precondition(activity.filtered(for: .attention).chats.count == 3)
    var shuffled = activity
    shuffled.allChats = [activity.allChats![4], activity.allChats![0], activity.allChats![3], activity.allChats![2], activity.allChats![1], .init(id: "mystery", name: "Mystery", workspace: "Test", agent: "codex", state: "unknown")]
    precondition(shuffled.filtered(for: .all).chats.map { $0.id } == ["input", "read", "unread", "working", "idle", "mystery"])
    precondition(shuffled.filtered(for: .ready).chats.map { $0.id } == ["read", "unread"])
    precondition(!HerdrStatus(status: "available", message: "", csrf: "test", chats: [activity.allChats![3]]).hasNewAttention(comparedTo: ready))
    precondition(PanelMode(rawValue: "invalid") == nil)
    precondition(ready.filtered(for: .unread).heading == "No unread replies.")
    let decoded = try! JSONDecoder().decode(HerdrStatus.self, from: Data(#"{"status":"available","message":"","csrf":"test","chats":[],"all_chats":[{"id":"test","name":"Chat","workspace":"Test","agent":"codex","state":"ready","unread":true}]}"#.utf8))
    precondition(decoded.filtered(for: .unread).chats.count == 1)
    precondition(decoded.allChats?.first?.pinned == nil)
    var bookmarked = shuffled
    bookmarked.allChats![0].pinned = true // idle
    bookmarked.allChats![1].pinned = true // read
    bookmarked.allChats![4].pinned = true // unread
    precondition(bookmarked.filtered(for: .pinned).chats.map { $0.id } == ["read", "unread", "idle"])
    precondition(bookmarked.filtered(for: .all).chats.map { $0.id } == ["read", "unread", "idle", "input", "working", "mystery"])
    precondition(bookmarked.filtered(for: .attention).chats.map { $0.id } == ["read", "unread", "input"])
    bookmarked.allChats![1].pinned = false
    precondition(bookmarked.filtered(for: .ready).chats.map { $0.id } == ["unread", "read"])
    precondition(bookmarked.filtered(for: .unread).chats.map { $0.id } == ["unread"])
    precondition(bookmarked.filtered(for: .input).chats.map { $0.id } == ["input"])
    precondition(ready.filtered(for: .pinned).heading == "No pinned chats.")
    let pinnedDecoded = try! JSONDecoder().decode(HerdrStatus.Chat.self, from: Data(#"{"id":"test","name":"Chat","workspace":"Test","agent":"codex","state":"idle","pinned":true}"#.utf8))
    precondition(pinnedDecoded.pinned == true)
    print("startup URL, appearance, and Herdr indicator checks passed")
    exit(0)
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSWindowDelegate {
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()
    private var server: Process?
    private var serverOutput = ""
    private var startupTimer: Timer?
    private var webView: WKWebView?
    private var dashboardOrigin: URL?
    private var updateController: UpdateController?
    private var isTerminating = false
    private var herdrTimer: Timer?
    private var herdrPolling = false
    private var herdrFocusing = false
    private var herdrPinning = false
    private var herdrStatusVersion = 0
    private var herdrStatus = HerdrStatus.unavailable
    private var herdrPanel: NSPanel?
    private var herdrModePicker: NSPopUpButton?
    private var herdrCount: NSTextField?
    private var herdrSignal: HerdrStatusMark?
    private var herdrDetail: NSTextField?
    private var herdrRows: NSStackView?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let menuIcon = Bundle.main.url(forResource: "MenuBarIcon", withExtension: "png")
            .flatMap { NSImage(contentsOf: $0) }
        menuIcon?.size = NSSize(width: 18, height: 18)
        menuIcon?.isTemplate = true
        statusItem.button?.image = menuIcon
        statusItem.button?.toolTip = "Worktree Monitor"
        statusItem.button?.target = self
        statusItem.button?.action = #selector(togglePopover)
        popover.behavior = .transient
        updateController = UpdateController(busyChecker: { [weak self] completion in
            guard let self else { completion(true); return }
            self.checkUpdateActivity(completion)
        }, changed: { [weak self] in self?.publishLoginItemStatus() })
        launchDashboard()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !popover.isShown, webView != nil { togglePopover() }
        return true
    }

    private func launchDashboard() {
        let executable = Bundle.main.resourceURL!.appendingPathComponent("backend/worktree-backend")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            showFailure("Cannot start Worktree Monitor", "The bundled backend is missing. Reinstall Worktree Monitor.")
            return
        }
        let process = Process()
        process.executableURL = executable
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        var environment = ProcessInfo.processInfo.environment
        environment["DASHBOARD_PORT"] = "0"
        var toolPaths = ["/usr/local/bin", "/opt/homebrew/bin", "\(NSHomeDirectory())/.local/bin"]
        for prefix in ["/opt/homebrew/opt", "/usr/local/opt"] {
            let entries = (try? FileManager.default.contentsOfDirectory(atPath: prefix)) ?? []
            for name in entries.filter({ $0.hasPrefix("node@") }).sorted(by: { $0.compare($1, options: .numeric) == .orderedDescending }) {
                toolPaths.append("\(prefix)/\(name)/bin")
            }
        }
        environment["PATH"] = (toolPaths + ["/usr/bin", "/bin", "/usr/sbin", "/sbin", environment["PATH"] ?? ""]).joined(separator: ":")
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self, weak process] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async {
                guard let self else { return }
                if self.webView == nil { self.serverOutput += text }
                if self.webView == nil,
                   let line = self.serverOutput.split(separator: "\n", omittingEmptySubsequences: false).first,
                   let url = dashboardURL(from: String(line)) {
                    self.startupTimer?.invalidate()
                    self.showDashboard(url)
                } else if self.serverOutput.contains("\n") && !self.serverOutput.hasPrefix("Workspace dashboard: ") {
                    self.startupTimer?.invalidate()
                    self.showFailure("Dashboard failed to start", String(self.serverOutput.prefix(1200)))
                    process?.terminate()
                }
            }
        }
        do {
            try process.run()
            server = process
            process.terminationHandler = { [weak self] process in
                DispatchQueue.main.async {
                    guard let self, !self.isTerminating else { return }
                    self.showFailure("Dashboard stopped", "The local dashboard process exited with status \(process.terminationStatus). Relaunch Worktree Monitor to try again.")
                }
            }
            startupTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in
                guard let self, self.webView == nil else { return }
                process.terminate()
                self.showFailure("Dashboard did not start", String(self.serverOutput.prefix(1200)).isEmpty ? "The local server did not report its address within 30 seconds." : String(self.serverOutput.prefix(1200)))
            }
        } catch {
            showFailure("Dashboard failed to start", error.localizedDescription)
        }
    }

    private func showDashboard(_ url: URL) {
        dashboardOrigin = url
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "app")
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = self
        webView = view
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1000, height: 650))
        root.wantsLayer = true
        view.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(view)
        NSLayoutConstraint.activate([
            view.topAnchor.constraint(equalTo: root.topAnchor), view.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: root.trailingAnchor), view.bottomAnchor.constraint(equalTo: root.bottomAnchor)
        ])
        root.frame.size = NSSize(width: 1000, height: 650)
        popover.contentViewController = NSViewController()
        popover.contentViewController?.view = root
        popover.contentSize = root.frame.size
        view.load(URLRequest(url: url))
        renderHerdrStatus()
        setHerdrPanelVisible(showHerdrPanel)
        refreshHerdrStatus()
        herdrTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.refreshHerdrStatus() }
        if !popover.isShown { togglePopover() }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url,
              let allowed = dashboardOrigin,
              url.scheme == allowed.scheme, url.host == allowed.host, url.port == allowed.port else {
            if let url = navigationAction.request.url, ["http", "https"].contains(url.scheme ?? ""), url.host != nil { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    @objc private func togglePopover() {
        if popover.isShown { popover.performClose(nil) }
        else if let button = statusItem.button { popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY) }
    }
    private var showHerdrPanel: Bool {
        UserDefaults.standard.object(forKey: "showHerdrPanel") as? Bool ?? true
    }

    private var panelMode: PanelMode {
        PanelMode(rawValue: UserDefaults.standard.string(forKey: "herdrPanelMode") ?? "") ?? .attention
    }
    private var displayedHerdrStatus: HerdrStatus { herdrStatus.filtered(for: panelMode) }

    private func setPanelMode(_ mode: PanelMode) {
        UserDefaults.standard.set(mode.rawValue, forKey: "herdrPanelMode")
        herdrModePicker?.selectItem(withTitle: mode.title)
        herdrSignal?.layer?.removeAnimation(forKey: "new-attention")
        renderHerdrStatus()
        publishLoginItemStatus()
    }

    @objc private func changeHerdrMode(_ sender: NSPopUpButton) {
        if let value = sender.selectedItem?.representedObject as? String, let mode = PanelMode(rawValue: value) { setPanelMode(mode) }
    }

    private func setHerdrPanelVisible(_ visible: Bool) {
        UserDefaults.standard.set(visible, forKey: "showHerdrPanel")
        if !visible { herdrPanel?.orderOut(nil); return }
        if herdrPanel == nil {
            let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 440, height: 300),
                                styleMask: [.titled, .closable, .utilityWindow, .nonactivatingPanel], backing: .buffered, defer: false)
            panel.title = "Herdr chats"
            panel.level = .floating
            panel.hidesOnDeactivate = false
            panel.isReleasedWhenClosed = false
            panel.isMovableByWindowBackground = true
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            panel.delegate = self
            panel.setFrameAutosaveName("HerdrAttentionPanel")
            if !panel.setFrameUsingName("HerdrAttentionPanel"), let screen = NSScreen.main {
                panel.setFrameTopLeftPoint(NSPoint(x: screen.visibleFrame.maxX - 460, y: screen.visibleFrame.maxY - 20))
            }
            let topLeft = NSPoint(x: panel.frame.minX, y: panel.frame.maxY)
            panel.setContentSize(NSSize(width: 440, height: 300))
            panel.setFrameTopLeftPoint(topLeft)
            let content = NSStackView()
            content.orientation = .vertical
            content.alignment = .leading
            content.spacing = 8
            content.edgeInsets = NSEdgeInsets(top: 12, left: 14, bottom: 12, right: 14)
            let modePicker = NSPopUpButton(frame: .zero, pullsDown: false)
            for mode in PanelMode.allCases {
                modePicker.addItem(withTitle: mode.title)
                modePicker.lastItem?.representedObject = mode.rawValue
            }
            modePicker.selectItem(withTitle: panelMode.title)
            modePicker.target = self; modePicker.action = #selector(changeHerdrMode)
            modePicker.setAccessibilityLabel("Panel mode")
            let signal = HerdrStatusMark(state: "unknown")
            let count = NSTextField(labelWithString: "Connecting…")
            count.font = .monospacedDigitSystemFont(ofSize: 14, weight: .semibold)
            count.textColor = PanelPalette.ink
            let spacer = NSView()
            let toolbar = NSStackView(views: [signal, count, spacer, modePicker])
            toolbar.spacing = 8; toolbar.alignment = .centerY
            count.setContentHuggingPriority(.required, for: .horizontal)
            modePicker.setContentHuggingPriority(.required, for: .horizontal)
            let detail = NSTextField(wrappingLabelWithString: "")
            detail.font = .systemFont(ofSize: 11)
            detail.textColor = PanelPalette.muted
            detail.isHidden = true
            NSLayoutConstraint.activate([
                signal.widthAnchor.constraint(equalToConstant: 20), signal.heightAnchor.constraint(equalToConstant: 20)
            ])
            let rows = NSStackView()
            rows.orientation = .vertical
            rows.alignment = .leading
            rows.spacing = 6
            rows.distribution = .fill
            rows.setHuggingPriority(.required, for: .vertical)
            let scroll = NSScrollView()
            scroll.drawsBackground = false
            scroll.hasVerticalScroller = true
            scroll.documentView = rows
            rows.translatesAutoresizingMaskIntoConstraints = false
            let footer = NSButton(title: "Open Herdr  ↗", target: self, action: #selector(openHerdrButton))
            footer.bezelStyle = .rounded
            [toolbar, detail, scroll, footer].forEach { content.addArrangedSubview($0) }
            panel.contentView = HerdrPanelBackground()
            panel.contentView!.addSubview(content)
            content.translatesAutoresizingMaskIntoConstraints = false
            NSLayoutConstraint.activate([
                content.topAnchor.constraint(equalTo: panel.contentView!.topAnchor),
                content.bottomAnchor.constraint(equalTo: panel.contentView!.bottomAnchor),
                content.leadingAnchor.constraint(equalTo: panel.contentView!.leadingAnchor),
                content.trailingAnchor.constraint(equalTo: panel.contentView!.trailingAnchor),
                toolbar.widthAnchor.constraint(equalTo: content.widthAnchor, constant: -28),
                detail.widthAnchor.constraint(equalTo: toolbar.widthAnchor),
                scroll.widthAnchor.constraint(equalTo: toolbar.widthAnchor),
                scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 75),
                rows.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
                rows.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
                rows.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor)
            ])
            herdrPanel = panel
            herdrModePicker = modePicker
            herdrCount = count
            herdrSignal = signal
            herdrDetail = detail
            herdrRows = rows
            renderHerdrStatus()
        }
        herdrPanel?.orderFrontRegardless()
    }

    private func renderHerdrStatus() {
        let status = displayedHerdrStatus
        guard let button = statusItem.button else { return }
        button.title = status.menuTitle
        statusItem.length = status.menuTitle.isEmpty ? NSStatusItem.squareLength : NSStatusItem.variableLength
        button.font = .monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
        button.imagePosition = .imageLeading
        button.toolTip = "Worktree Monitor · \(panelMode.title) · \(status.heading)"
        button.setAccessibilityLabel(button.toolTip)
        herdrCount?.stringValue = status.available ? "\(status.chats.count) \(status.chats.count == 1 ? "chat" : "chats")" : "Unavailable"
        herdrCount?.textColor = status.needsInput ? PanelPalette.attentionInk : PanelPalette.ink
        herdrSignal?.setState(status.indicatorState)
        herdrDetail?.stringValue = herdrPinning ? "Saving pin…" : status.available ? "" : status.message
        herdrDetail?.isHidden = status.available && !herdrPinning
        herdrModePicker?.isEnabled = !herdrPinning && !herdrFocusing
        guard let rows = herdrRows else { return }
        rows.arrangedSubviews.forEach { rows.removeArrangedSubview($0); $0.removeFromSuperview() }
        for chat in status.chats {
            let state = ["blocked": "Needs input", "ready": panelMode == .unread ? "Unread reply" : "Ready for reply", "working": "Working", "idle": "Idle", "unknown": "Status unknown"][chat.state] ?? "Status unknown"
            let row = HerdrChatButton(frame: .zero)
            row.cell = HerdrChatCell(textCell: "")
            row.target = self; row.action = #selector(openHerdrChat)
            row.setStatus(chat.state)
            let paragraph = NSMutableParagraphStyle(); paragraph.alignment = .left; paragraph.lineSpacing = 3; paragraph.lineBreakMode = .byTruncatingTail
            let title = NSMutableAttributedString(string: "\(chat.workspace) · \(chat.name)\n", attributes: [.font: NSFont.systemFont(ofSize: 12, weight: .semibold), .foregroundColor: PanelPalette.ink, .paragraphStyle: paragraph])
            title.append(NSAttributedString(string: "\(chat.agent.capitalized) · \(state)\(chat.unread == true ? " · Unread" : "")", attributes: [.font: NSFont.monospacedSystemFont(ofSize: 9, weight: .regular), .foregroundColor: PanelPalette.muted, .paragraphStyle: paragraph]))
            row.attributedTitle = title
            row.identifier = NSUserInterfaceItemIdentifier(chat.id)
            row.isBordered = false
            row.alignment = .left
            row.lineBreakMode = .byTruncatingTail
            row.cell?.wraps = true
            row.toolTip = "Open \(chat.name) in \(chat.workspace) — \(state)"
            row.setAccessibilityLabel("\(row.toolTip ?? "Chat")\(chat.unread == true ? " · Unread" : "")")
            row.isEnabled = status.available && !herdrPinning && !herdrFocusing
            let pin = NSButton(image: NSImage(systemSymbolName: chat.pinned == true ? "star.fill" : "star", accessibilityDescription: nil) ?? NSImage(), target: self, action: #selector(toggleHerdrPin))
            pin.isBordered = false
            pin.contentTintColor = chat.pinned == true ? PanelPalette.ink : PanelPalette.muted
            pin.identifier = NSUserInterfaceItemIdentifier(chat.id)
            pin.toolTip = "\(chat.pinned == true ? "Unpin" : "Pin") \(chat.name) in \(chat.workspace)"
            pin.setAccessibilityLabel(pin.toolTip)
            pin.isEnabled = row.isEnabled
            let container = NSView()
            container.addSubview(row); container.addSubview(pin)
            row.translatesAutoresizingMaskIntoConstraints = false
            pin.translatesAutoresizingMaskIntoConstraints = false
            rows.addArrangedSubview(container)
            NSLayoutConstraint.activate([
                container.widthAnchor.constraint(equalTo: rows.widthAnchor),
                container.heightAnchor.constraint(equalToConstant: 56),
                row.leadingAnchor.constraint(equalTo: container.leadingAnchor),
                row.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                row.topAnchor.constraint(equalTo: container.topAnchor),
                row.bottomAnchor.constraint(equalTo: container.bottomAnchor),
                pin.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -10),
                pin.centerYAnchor.constraint(equalTo: container.centerYAnchor),
                pin.widthAnchor.constraint(equalToConstant: 32),
                pin.heightAnchor.constraint(equalToConstant: 32)
            ])
        }
        if status.available && status.chats.isEmpty {
            let empty = NSTextField(wrappingLabelWithString: (herdrStatus.allChats ?? herdrStatus.chats).isEmpty ? "No chats detected in Herdr." : "No chats match this view.")
            empty.font = .systemFont(ofSize: 12); empty.textColor = PanelPalette.muted
            rows.addArrangedSubview(empty)
            empty.widthAnchor.constraint(equalTo: rows.widthAnchor).isActive = true
        }
    }


    private func refreshHerdrStatus() {
        guard let origin = dashboardOrigin, !herdrPolling, !herdrPinning, !isTerminating else { return }
        let version = herdrStatusVersion
        herdrPolling = true
        var request = URLRequest(url: origin.appendingPathComponent("api/herdr"), cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4)
        request.httpMethod = "GET"
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            let status = (response as? HTTPURLResponse)?.statusCode == 200 ? data.flatMap { try? JSONDecoder().decode(HerdrStatus.self, from: $0) } : nil
            DispatchQueue.main.async {
                guard let self, !self.isTerminating else { return }
                self.herdrPolling = false
                guard version == self.herdrStatusVersion else { self.refreshHerdrStatus(); return }
                let next = status ?? .unavailable
                if next != self.herdrStatus {
                    let hasNewAttention = next.filtered(for: self.panelMode).hasNewAttention(comparedTo: self.displayedHerdrStatus)
                    self.herdrStatus = next; self.renderHerdrStatus()
                    if hasNewAttention && self.showHerdrPanel && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
                        let pulse = CABasicAnimation(keyPath: "opacity")
                        pulse.fromValue = 0.3; pulse.toValue = 1; pulse.duration = 0.8; pulse.autoreverses = true; pulse.repeatCount = 3
                        self.herdrSignal?.layer?.add(pulse, forKey: "new-attention")
                    }
                    if self.displayedHerdrStatus.chats.isEmpty { self.herdrSignal?.layer?.removeAnimation(forKey: "new-attention") }
                }
            }
        }.resume()
    }

    @objc private func openHerdrChat(_ sender: NSButton) {
        guard let pane = sender.identifier?.rawValue, displayedHerdrStatus.chats.contains(where: { $0.id == pane }),
              let origin = dashboardOrigin, !herdrFocusing, !herdrPinning, displayedHerdrStatus.available else { return }
        herdrFocusing = true
        renderHerdrStatus()
        herdrDetail?.isHidden = false
        herdrDetail?.stringValue = "Opening chat…"
        var request = URLRequest(url: origin.appendingPathComponent("api/herdr/focus"), timeoutInterval: 6)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("http://127.0.0.1:\(origin.port!)", forHTTPHeaderField: "Origin")
        request.setValue(herdrStatus.csrf, forHTTPHeaderField: "X-CSRF-Token")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["id": pane])
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            let success = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async {
                guard let self, !self.isTerminating else { return }
                self.herdrFocusing = false
                self.renderHerdrStatus()
                self.herdrDetail?.isHidden = success
                self.herdrDetail?.stringValue = success ? "" : "Could not select that chat. It may have changed; refresh or open Herdr."
                if success { self.openHerdrButton() }
                self.refreshHerdrStatus()
            }
        }.resume()
    }

    @objc private func toggleHerdrPin(_ sender: NSButton) {
        guard let pane = sender.identifier?.rawValue,
              let chat = displayedHerdrStatus.chats.first(where: { $0.id == pane }),
              displayedHerdrStatus.available, let origin = dashboardOrigin,
              !herdrPinning, !herdrFocusing else { return }
        herdrPinning = true
        herdrStatusVersion += 1
        renderHerdrStatus()
        var request = URLRequest(url: origin.appendingPathComponent("api/herdr/pin"), timeoutInterval: 6)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("http://127.0.0.1:\(origin.port!)", forHTTPHeaderField: "Origin")
        request.setValue(herdrStatus.csrf, forHTTPHeaderField: "X-CSRF-Token")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["id": pane, "pinned": chat.pinned != true])
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            let success = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async {
                guard let self, !self.isTerminating else { return }
                self.herdrPinning = false
                self.renderHerdrStatus()
                if success {
                    self.refreshHerdrStatus()
                } else {
                    self.herdrDetail?.isHidden = false
                    self.herdrDetail?.stringValue = "Could not save that pin. Refresh before trying again."
                }
            }
        }.resume()
    }

    @objc private func openHerdrButton() {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "so.pen.herdr-gpui") else {
            herdrDetail?.isHidden = false
            herdrDetail?.stringValue = "Herdr is not installed in Applications."
            return
        }
        NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration()) { [weak self] _, error in
            if error != nil { DispatchQueue.main.async { self?.herdrDetail?.isHidden = false; self?.herdrDetail?.stringValue = "Could not open Herdr. Try opening it from Applications." } }
        }
    }

    func windowWillClose(_ notification: Notification) {
        if notification.object as? NSPanel === herdrPanel, !isTerminating {
            UserDefaults.standard.set(false, forKey: "showHerdrPanel")
            publishLoginItemStatus()
        }
    }

    private func setLaunchAtLogin(_ enabled: Bool) {
        do {
            if enabled { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
            publishLoginItemStatus()
        } catch {
            publishLoginItemStatus(error: error.localizedDescription)
        }
    }

    private func publishLoginItemStatus(error: String? = nil) {
        let service = SMAppService.mainApp
        var message = ""
        if let error {
            message = "Could not update login setting: \(error)"
        } else {
            switch service.status {
            case .enabled:
                message = "Enabled"
            case .requiresApproval:
                message = "Allow in System Settings → General → Login Items"
            case .notFound:
                message = "Not registered yet"
            case .notRegistered:
                message = "Disabled"
            @unknown default:
                message = "Login item status unavailable"
            }
        }
        let resolvedStatus: String
        switch service.status {
        case .enabled: resolvedStatus = "enabled"
        case .requiresApproval: resolvedStatus = "requiresApproval"
        case .notRegistered: resolvedStatus = "notRegistered"
        case .notFound: resolvedStatus = "notFound"
        @unknown default: resolvedStatus = "unknown"
        }
        publishSettings(enabled: service.status == .enabled, status: resolvedStatus, message: message)
    }

    private func checkUpdateActivity(_ completion: @escaping (Bool) -> Void) {
        guard let origin = dashboardOrigin, !isTerminating else { completion(true); return }
        struct Activity: Decodable { let busy: Bool }
        let request = URLRequest(url: origin.appendingPathComponent("api/activity"), cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4)
        URLSession.shared.dataTask(with: request) { data, response, _ in
            let activity = (response as? HTTPURLResponse)?.statusCode == 200 ? data.flatMap { try? JSONDecoder().decode(Activity.self, from: $0) } : nil
            DispatchQueue.main.async { completion(activity?.busy ?? true) }
        }.resume()
    }

    private func publishSettings(enabled: Bool, status: String, message: String) {
        guard let webView,
              let data = try? JSONSerialization.data(withJSONObject: ["enabled": enabled, "status": status, "message": message, "available": true, "herdrPanel": showHerdrPanel, "herdrPanelMode": panelMode.rawValue,
                  "version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
                  "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
                  "updateAvailable": updateController?.available ?? false,
                  "canCheckForUpdates": updateController?.canCheckForUpdates ?? false,
                  "automaticUpdateChecks": updateController?.automaticUpdateChecks ?? false,
                  "updatesMessage": updateController?.message ?? "Updates are unavailable."]),
              let detail = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-settings',{detail:\(detail)}))")
    }

    private func showPhonePairing() {
        guard let origin = dashboardOrigin else { return }
        let request = URLRequest(url: origin.appendingPathComponent("api/phone"), cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4)
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            let info = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                guard let self, !self.isTerminating else { return }
                guard (response as? HTTPURLResponse)?.statusCode == 200,
                      let value = info?["pairing_url"] as? String, let url = URL(string: value),
                      url.scheme == "http", url.path == "/deck", url.fragment?.hasPrefix("pair=") == true,
                      let filter = CIFilter(name: "CIQRCodeGenerator") else {
                    self.webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-phone-error',{detail:'Create a new pairing link first.'}))")
                    return
                }
                filter.setValue(Data(value.utf8), forKey: "inputMessage")
                filter.setValue("M", forKey: "inputCorrectionLevel")
                guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)) else { return }
                let representation = NSCIImageRep(ciImage: output)
                let image = NSImage(size: representation.size); image.addRepresentation(representation)
                let preview = NSImageView(frame: NSRect(x: 0, y: 0, width: 250, height: 250))
                preview.image = image; preview.imageScaling = .scaleProportionallyUpOrDown
                preview.setAccessibilityLabel("Scan this code with your phone camera to pair")
                let alert = NSAlert()
                alert.messageText = "Pair your phone"
                alert.informativeText = "Scan with your phone camera on the same trusted Wi-Fi. This one-time link expires in 5 minutes. It grants access to chat status and selection on this Mac over local HTTP."
                alert.accessoryView = preview; alert.addButton(withTitle: "Done")
                NSApp.activate(ignoringOtherApps: true)
                alert.runModal()
            }
        }.resume()
    }

    private func publishDockerOpenError() {
        webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-docker-error',{detail:'Could not open Docker Desktop. Check that it is installed in Applications.'}))")
    }

    private func chooseDirectory(requestId: String, purpose: String) {
        NSApp.activate(ignoringOtherApps: true)
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.message = purpose == "showpass" ? "Choose the Showpass base checkout." : "Choose a folder to search for projects."
        panel.prompt = "Choose Folder"
        let result = panel.runModal()
        let path: Any = result == .OK ? (panel.url?.path as Any? ?? NSNull()) : NSNull()
        if let data = try? JSONSerialization.data(withJSONObject: ["requestId": requestId, "purpose": purpose, "path": path]),
           let detail = String(data: data, encoding: .utf8) {
            webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-directory',{detail:\(detail)}))")
        }
        if !popover.isShown { togglePopover() }
    }

    private func showFailure(_ title: String, _ message: String) {
        guard !isTerminating else { return }
        isTerminating = true
        updateController?.stop()
        startupTimer?.invalidate()
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        isTerminating = true
        startupTimer?.invalidate()
        herdrTimer?.invalidate()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "app")
        if let server, server.isRunning { server.terminate() }
    }
}

extension AppDelegate: WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "app", message.frameInfo.isMainFrame,
              let origin = dashboardOrigin,
              message.frameInfo.securityOrigin.protocol == origin.scheme,
              message.frameInfo.securityOrigin.host == origin.host,
              Int(message.frameInfo.securityOrigin.port) == origin.port,
              let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }

        switch action {
        case "settings":
            guard Set(body.keys) == ["action"] else { return }
            publishLoginItemStatus()
        case "setAppearance":
            guard Set(body.keys) == ["action", "appearance"],
                  let mode = body["appearance"] as? String,
                  ["system", "light", "dark"].contains(mode) else { return }
            NSApp.appearance = appearanceName(for: mode).flatMap { NSAppearance(named: $0) }
            popover.appearance = NSApp.appearance
        case "setHerdrPanelMode":
            guard Set(body.keys) == ["action", "mode"], let value = body["mode"] as? String,
                  let mode = PanelMode(rawValue: value) else { return }
            setPanelMode(mode)
        case "setHerdrPanel":
            guard Set(body.keys) == ["action", "enabled"], let enabled = body["enabled"] as? NSNumber,
                  CFGetTypeID(enabled) == CFBooleanGetTypeID() else { return }
            setHerdrPanelVisible(enabled.boolValue)
            publishLoginItemStatus()
        case "checkForUpdates":
            guard Set(body.keys) == ["action"] else { return }
            updateController?.checkForUpdates()
        case "setAutomaticUpdateChecks":
            guard Set(body.keys) == ["action", "enabled"], let enabled = body["enabled"] as? NSNumber,
                  CFGetTypeID(enabled) == CFBooleanGetTypeID() else { return }
            updateController?.setAutomaticUpdateChecks(enabled.boolValue)
        case "setLaunchAtLogin":
            guard Set(body.keys) == ["action", "enabled"], let enabled = body["enabled"] as? NSNumber,
                  CFGetTypeID(enabled) == CFBooleanGetTypeID() else { return }
            setLaunchAtLogin(enabled.boolValue)
        case "chooseDirectory":
            guard Set(body.keys) == ["action", "requestId", "purpose"],
                  let requestId = body["requestId"] as? String, !requestId.isEmpty, requestId.count <= 128,
                  let purpose = body["purpose"] as? String, ["workspace", "showpass"].contains(purpose) else { return }
            chooseDirectory(requestId: requestId, purpose: purpose)
        case "showPhonePairing":
            guard Set(body.keys) == ["action"] else { return }
            showPhonePairing()
        case "openDeck":
            guard Set(body.keys) == ["action"], let origin = dashboardOrigin else { return }
            NSWorkspace.shared.open(origin.appendingPathComponent("deck"))
        case "openDocker":
            guard Set(body.keys) == ["action"] else { return }
            guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.docker.docker") else {
                publishDockerOpenError()
                return
            }
            NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration()) { [weak self] _, error in
                if error != nil { DispatchQueue.main.async { self?.publishDockerOpenError() } }
            }
        case "openBrowser":
            guard Set(body.keys) == ["action"] else { return }
            if let url = webView?.url, url.scheme == "http", url.host == origin.host, url.port == origin.port {
                NSWorkspace.shared.open(url)
            }
        case "quit":
            guard Set(body.keys) == ["action"] else { return }
            NSApp.terminate(nil)
        default:
            return
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
