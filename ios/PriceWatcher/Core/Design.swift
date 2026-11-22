import Charts
import SwiftUI

enum PW {
    static let canvas = color(0x0C1017)
    static let surface = color(0x141A24)
    static let surfaceRaised = color(0x1B2431)
    static let stroke = color(0x293546)
    static let text = color(0xEDF2FA)
    static let muted = color(0xA3AFC2)
    static let accent = color(0xB4A7EF)
    static let accentSurface = color(0x29263D)
    static let accentStroke = color(0x4B4367)
    static let heroSurface = color(0x1C2233)
    static let decrease = color(0x85CCB5)
    static let increase = color(0xEC9DA6)
    static let warning = color(0xE3BD9D)
    static let pagePadding: CGFloat = 20

    private static func color(_ rgb: UInt32) -> Color {
        Color(
            red: Double((rgb >> 16) & 0xFF) / 255,
            green: Double((rgb >> 8) & 0xFF) / 255,
            blue: Double(rgb & 0xFF) / 255
        )
    }
}

extension View {
    func card() -> some View {
        self.padding(17)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(PW.surface, in: RoundedRectangle(cornerRadius: 22))
            .overlay {
                RoundedRectangle(cornerRadius: 22).strokeBorder(PW.stroke, lineWidth: 1)
                    .allowsHitTesting(false)
            }
    }

    func page() -> some View {
        self.padding(.horizontal, PW.pagePadding)
            .padding(.vertical, 16)
            .frame(maxWidth: 580)
            .frame(maxWidth: .infinity)
    }
}

struct BrandMark: View {
    let size: CGFloat
    var body: some View {
        Image(systemName: "chart.line.downtrend.xyaxis")
            .font(.system(size: size * 0.48, weight: .semibold))
            .foregroundStyle(PW.accent)
            .frame(width: size, height: size)
            .background(PW.accentSurface, in: RoundedRectangle(cornerRadius: size * 0.28))
            .overlay {
                RoundedRectangle(cornerRadius: size * 0.28).strokeBorder(PW.accentStroke, lineWidth: 1)
            }
            .accessibilityHidden(true)
    }
}

struct PrimaryButton: ButtonStyle {
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: 14)
        configuration.label.font(.headline).padding(.vertical, 14).padding(.horizontal, 16)
            .foregroundStyle(enabled ? PW.accent : PW.muted)
            .background(enabled && !configuration.isPressed ? PW.accentSurface : PW.surface, in: shape)
            .overlay { shape.strokeBorder(enabled ? PW.accentStroke : PW.stroke).allowsHitTesting(false) }
    }
}

struct SectionTitle: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View { Text(text).font(.title3.weight(.semibold)).accessibilityAddTraits(.isHeader) }
}

struct SelectionStrip<Value: CaseIterable & Hashable & Identifiable>: View where Value.AllCases: RandomAccessCollection {
    @Binding var selection: Value
    let title: (Value) -> String

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                ForEach(Value.allCases) { value in
                    Button(title(value)) { selection = value }
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 12)
                        .frame(minHeight: 44)
                        .foregroundStyle(value == selection ? PW.accent : PW.muted)
                        .background(value == selection ? PW.accentSurface : PW.surface, in: Capsule())
                        .overlay {
                            Capsule().strokeBorder(value == selection ? PW.accentStroke : .clear)
                                .allowsHitTesting(false)
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(value == selection ? .isSelected : [])
                }
            }
        }
        .scrollIndicators(.hidden)
    }
}

struct PriceDelta: View {
    let change: PriceChange
    var body: some View {
        let (symbol, color): (String, Color) = switch change.direction {
        case "down": ("arrow.down.right", PW.decrease)
        case "up": ("arrow.up.right", PW.increase)
        default: ("minus", PW.muted)
        }
        Label {
            Text("\(change.changeText) · \(change.percentText)")
        } icon: {
            Image(systemName: symbol)
        }
        .font(.footnote.weight(.semibold))
        .foregroundStyle(color)
    }
}

struct LoadFailure: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(message, systemImage: "exclamationmark.circle").foregroundStyle(PW.warning)
                .font(.subheadline)
            Button("Повторить", systemImage: "arrow.clockwise", action: retry)
                .font(.subheadline.weight(.semibold)).frame(minHeight: 44)
        }
        .card()
    }
}

struct PriceChart: View {
    let points: [PricePoint]
    var compact = false
    @State private var selected: Int?

    private struct Mark: Identifiable {
        let index: Int
        let segment: Int
        let point: PricePoint
        let value: Int
        var id: Int { index }
    }

    private var marks: [Mark] {
        var segment = 0
        return points.enumerated().compactMap { index, point in
            guard let value = point.valueMinor else {
                segment += 1
                return nil
            }
            return Mark(index: index, segment: segment, point: point, value: value)
        }
    }

    var body: some View {
        let marks = marks
        if !marks.isEmpty {
            if compact {
                chart(marks).chartXAxis(.hidden).chartYAxis(.hidden)
                    .frame(height: 34).accessibilityHidden(true)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    chart(marks)
                        .chartXSelection(value: $selected)
                        .chartXAxis(.hidden)
                        .chartYAxis(.hidden)
                        .frame(height: 126)
                        .accessibilityLabel("График цен")
                    HStack {
                        if let first = points.first { Text(Prices.date(first.date)) }
                        Spacer()
                        if let last = points.last { Text(Prices.date(last.date)) }
                    }
                    .font(.caption).foregroundStyle(PW.muted)
                    Text(points.indices.contains(selected ?? -1) ? points[selected ?? 0].label : marks.last?.point.label ?? "")
                        .font(.subheadline.weight(.semibold))
                }
            }
        } else if !compact {
            Label("Истории пока нет", systemImage: "chart.xyaxis.line")
                .font(.subheadline).foregroundStyle(PW.muted).padding(.vertical, 12)
        }
    }

    private func chart(_ marks: [Mark]) -> some View {
        Chart {
            ForEach(marks) { mark in
                LineMark(x: .value("День", mark.index), y: .value("Цена в копейках", mark.value),
                         series: .value("Период", mark.segment))
                    .foregroundStyle(PW.accent)
                    .lineStyle(StrokeStyle(lineWidth: compact ? 2 : 2.5, lineCap: .round))
                    .accessibilityLabel(mark.point.label)
                if !compact || marks.count == 1 {
                    PointMark(x: .value("День", mark.index), y: .value("Цена в копейках", mark.value))
                        .symbolSize(compact ? 18 : 10)
                        .foregroundStyle(PW.accent)
                        .accessibilityHidden(true)
                }
            }
            if !compact, let selected, points.indices.contains(selected) {
                RuleMark(x: .value("Выбранный день", selected)).foregroundStyle(PW.muted.opacity(0.6))
                    .accessibilityHidden(true)
            }
        }
        .chartXScale(domain: 0...max(1, points.count - 1))
        .chartYScale(domain: .automatic(includesZero: false))
    }
}
