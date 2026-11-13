import AntifraudCore
import SwiftUI

/// Каталог схем по категориям с поиском (Schemes.jsx).
struct SchemesView: View {
    @Environment(AppModel.self) private var model
    @State private var schemes: [SchemeBrief]?
    @State private var error: String?
    @State private var query = ""

    var body: some View {
        List {
            if let error { ErrorText(text: error) }
            if schemes != nil {
                Section {
                    Text("По этим схемам агент узнаёт мошенника: каждая — легенда и этапы с типичными фразами.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            ForEach(groups, id: \.0) { category, list in
                Section {
                    ForEach(list) { s in
                        NavigationLink(value: Route.scheme(s.id)) {
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text(s.name).font(.headline)
                                    Spacer()
                                    Text(Format.count(s.stages.count, (one: "этап", few: "этапа", many: "этапов")))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Text(s.legend).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                    }
                } header: {
                    HStack {
                        Text(category.title)
                        Spacer()
                        Text("\(list.count)")
                    }
                }
            }
            if schemes != nil, groups.isEmpty { Text("Ничего не нашлось.").foregroundStyle(.secondary) }
            if schemes == nil, error == nil { ProgressView() }
        }
        .navigationTitle("Схемы")
        .searchable(text: $query, prompt: "Найти: «безопасный счёт», «внук», «Госуслуги»")
        .refreshable { await load() }
        .task(id: model.config) { await load() }
    }

    private var groups: [(SchemeCategory, [SchemeBrief])] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        let found = (schemes ?? []).filter { s in
            needle.isEmpty || ([s.name, s.legend] + s.aliases).contains { $0.lowercased().contains(needle) }
        }
        return SchemeCategory.allCases.map { cat in (cat, found.filter { $0.category == cat }) }.filter { !$0.1.isEmpty }
    }

    private func load() async {
        guard let client = model.client else { return }
        do {
            schemes = try await client.schemes()
            error = nil
        } catch {
            self.error = CallSession.message(error)
        }
    }
}

/// Карточка схемы с этапами (SchemeDetail в Schemes.jsx).
struct SchemeDetailView: View {
    @Environment(AppModel.self) private var model
    let id: String
    @State private var scheme: Scheme?
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let error { ErrorText(text: error) }
                if let s = scheme {
                    Text(s.name).font(.title2.weight(.bold))
                    Text(s.description)
                    Card {
                        VStack(spacing: 8) {
                            PropRow(label: "Категория") { Text(s.category.title) }
                            PropRow(label: "Кем представляются") { Text(s.legend) }
                            if !s.aliases.isEmpty { PropRow(label: "Ещё называют") { Text(s.aliases.joined(separator: "; ")) } }
                        }
                        .font(.subheadline)
                        Text("На что давят").font(.caption).foregroundStyle(.secondary)
                        Chips(items: s.pressurePoints)
                    }
                    Text("Этапы").font(.title3.weight(.semibold))
                    ForEach(s.stages, id: \.n) { st in stage(st) }
                    Text("Как тянуть время").font(.title3.weight(.semibold))
                    ForEach(s.stallTactics, id: \.self) { t in
                        Label(t, systemImage: "hourglass").font(.subheadline)
                    }
                    if !s.sources.isEmpty {
                        Text("Источники").font(.title3.weight(.semibold))
                        ForEach(s.sources, id: \.self) { u in
                            if let url = URL(string: u) {
                                Link(destination: url) { Label(Self.host(url), systemImage: "arrow.up.right.square") }
                                    .font(.subheadline)
                            }
                        }
                    }
                } else if error == nil {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding()
        }
        .navigationTitle(scheme?.name ?? id)
        .inlineTitle()
        .task(id: id) {
            do {
                scheme = try await model.client?.scheme(id)
                error = nil
            } catch {
                self.error = CallSession.message(error)
            }
        }
    }

    private func stage(_ st: Stage) -> some View {
        Card("\(st.n). \(st.name)") {
            Text("Цель: \(st.goal)").font(.subheadline).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 4) {
                ForEach(st.typicalPhrases, id: \.self) { p in
                    Text("«\(p)»").font(.subheadline).italic()
                }
            }
            if !st.redFlags.isEmpty {
                Text("Признаки").font(.caption).foregroundStyle(.secondary)
                Chips(items: st.redFlags, tone: .red)
            }
            if !st.asksFor.isEmpty {
                Text("Просят").font(.caption).foregroundStyle(.secondary)
                Chips(items: st.asksFor)
            }
            HStack(alignment: .firstTextBaseline) {
                Text("Дальше").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(st.nextMove).font(.subheadline)
            }
        }
    }

    /// Адреса в punycode, которые система не раскодирует сама.
    static func host(_ url: URL) -> String {
        let h = (url.host ?? url.absoluteString).replacingOccurrences(of: "www.", with: "")
        return ["xn--b1aew.xn--p1ai": "мвд.рф", "xn--d1abbgf6aiiy.xn--p1ai": "президент.рф"][h] ?? h
    }
}
