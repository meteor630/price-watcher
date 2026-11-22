import Charts
import SwiftUI

enum PW {
    static let canvas = Color(red: 0.06, green: 0.08, blue: 0.08)
    static let surface = Color(red: 0.105, green: 0.13, blue: 0.13)
    static let stroke = Color(red: 0.20, green: 0.24, blue: 0.23)
    static let accent = Color(red: 0.81, green: 0.96, blue: 0.43)
    static let ink = Color(red: 0.11, green: 0.15, blue: 0.065)
    static let muted = Color(red: 0.67, green: 0.72, blue: 0.70)
    static let warning = Color(red: 1, green: 0.75, blue: 0.58)
    static let pagePadding: CGFloat = 20
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
            .foregroundStyle(PW.ink)
            .frame(width: size, height: size)
            .background(PW.accent, in: RoundedRectangle(cornerRadius: size * 0.28))
            .accessibilityHidden(true)
    }
}

struct PrimaryButton: ButtonStyle {
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.headline).padding(.vertical, 14).padding(.horizontal, 16)
            .foregroundStyle(PW.ink)
            .background(PW.accent.opacity(enabled ? (configuration.isPressed ? 0.78 : 1) : 0.45),
                        in: RoundedRectangle(cornerRadius: 15))
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
                        .foregroundStyle(value == selection ? PW.ink : PW.muted)
                        .background(value == selection ? PW.accent : PW.surface, in: Capsule())
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
    var hero = false

    var body: some View {
        let symbol = change.direction == "down" ? "arrow.down.right" : change.direction == "up" ? "arrow.up.right" : "minus"
        Label {
            Text("\(change.changeText) · \(change.percentText)")
        } icon: {
            Image(systemName: symbol)
        }
        .font(.footnote.weight(.semibold))
        .foregroundStyle(hero ? PW.ink : change.direction == "up" ? PW.warning : PW.accent)
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
