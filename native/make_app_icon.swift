import AppKit

func color(_ hex: UInt32) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 0xff) / 255,
            green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: 1)
}

func drawMark(monochrome: Bool) {
    let ring = NSBezierPath(ovalIn: CGRect(x: 4, y: 4, width: 20, height: 20))
    ring.lineWidth = 2.2
    (monochrome ? NSColor.black : color(0x86b99a)).setStroke()
    ring.stroke()
    let signal = NSBezierPath()
    signal.move(to: CGPoint(x: 4, y: 14))
    for point in [CGPoint(x: 10, y: 14), CGPoint(x: 13, y: 8),
                  CGPoint(x: 17, y: 20), CGPoint(x: 20, y: 14), CGPoint(x: 24, y: 14)] {
        signal.line(to: point)
    }
    signal.lineWidth = 2.4
    signal.lineCapStyle = .round
    signal.lineJoinStyle = .round
    (monochrome ? NSColor.black : color(0x193f36)).setStroke()
    signal.stroke()
    (monochrome ? NSColor.black : color(0xdb705f)).setFill()
    NSBezierPath(ovalIn: CGRect(x: 21.7, y: 11.7, width: 4.6, height: 4.6)).fill()
}

func drawIcon(size: Int, menuBar: Bool = false, homeScreen: Bool = false) -> NSBitmapImageRep {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                  isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0,
                                  bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let context = NSGraphicsContext.current!.cgContext
    context.clear(CGRect(x: 0, y: 0, width: size, height: size))
    context.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)
    if !menuBar {
        let tile = NSBezierPath(roundedRect: CGRect(x: 0, y: 0, width: 1024, height: 1024), xRadius: homeScreen ? 0 : 228, yRadius: homeScreen ? 0 : 228)
        NSGradient(starting: .white, ending: color(0xeaf1ec))!.draw(in: tile, angle: -90)
    }
    let inset: CGFloat = menuBar ? 0 : 128
    let extent: CGFloat = menuBar ? 1024 : 768
    context.translateBy(x: inset, y: 1024 - inset)
    context.scaleBy(x: extent / 28, y: -extent / 28)
    drawMark(monochrome: menuBar)
    NSGraphicsContext.restoreGraphicsState()
    return bitmap
}

let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let iconset = root.appendingPathComponent("AppIcon.iconset", isDirectory: true)
try? FileManager.default.removeItem(at: iconset)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
let sizes = [("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
             ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
             ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
             ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
             ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024)]
for (name, size) in sizes {
    let data = drawIcon(size: size).representation(using: .png, properties: [:])!
    try data.write(to: iconset.appendingPathComponent(name))
}
try Data(contentsOf: iconset.appendingPathComponent("icon_512x512@2x.png")).write(to: root.appendingPathComponent("AppIcon.png"))
let smallPreview = drawIcon(size: 256).representation(using: .png, properties: [:])!
try smallPreview.write(to: root.appendingPathComponent("AppIcon-preview.png"))
let menuBitmap = drawIcon(size: 36, menuBar: true)
precondition(menuBitmap.colorAt(x: 0, y: 0)!.alphaComponent == 0)
precondition(menuBitmap.colorAt(x: 18, y: 5)!.alphaComponent > 0)
let menuIcon = menuBitmap.representation(using: .png, properties: [:])!
try menuIcon.write(to: root.appendingPathComponent("MenuBarIcon.png"))

for size in [180, 512] {
    try drawIcon(size: size, homeScreen: true).representation(using: .png, properties: [:])!.write(to: root.deletingLastPathComponent().appendingPathComponent("deck-icon-\(size).png"))
}
