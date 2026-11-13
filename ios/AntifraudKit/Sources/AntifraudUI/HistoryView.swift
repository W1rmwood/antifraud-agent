import AntifraudCore
import SwiftUI

/// История: кейсы из базы и звонки из data/runs/ по месяцам, поиск по номеру (History.jsx).
struct HistoryView: View {
    @Environment(AppModel.self) private var model
    @State private var items: [HistoryItem]?
    @State private var error: String?
    @State private var query = ""
    @State private var queryError: String?

    var body: some View {
        List {
            if let error { ErrorText(text: error) }
            if let queryError { Text(queryError).foregroundStyle(.red).font(.subheadline) }
            if let items {
                Section {
                    Text("\(Format.count(items.count, (one: "звонок", few: "звонка", many: "звонков"))), новые сверху. Кейс сохраняет агент в конце звонка; записи, проигранные без модели, помечены.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                HistoryRows(items: items)
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle("История")
        .searchable(text: $query, prompt: "Найти по номеру: 8 999 000-00-01")
        .onSubmit(of: .search) { search() }
        .refreshable { await load() }
        .task(id: model.config) { await load() }
    }

    private func search() {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        do {
            guard let phone = try PhoneNumber.normalize(q) else { return }
            queryError = nil
            model.historyPath.append(.number(phone))
        } catch {
            queryError = error.errorDescription
        }
    }

    private func load() async {
        guard let client = model.client else { return }
        do {
            async let cases = (try? await client.cases(limit: 200)) ?? []
            async let runs = client.runs()
            items = mergeHistory(cases: await cases, runs: try await runs)
            error = nil
        } catch {
            self.error = CallSession.message(error)
        }
    }
}

/// Звонки по месяцам: заголовок группы и счётчик.
struct HistoryRows: View {
    @Environment(AppModel.self) private var model
    var items: [HistoryItem]

    var body: some View {
        if items.isEmpty {
            Text("Звонков пока нет.").foregroundStyle(.secondary)
        }
        ForEach(groups, id: \.0) { month, list in
            Section {
                ForEach(list) { item in
                    NavigationLink(value: item.noReport ? Route.call(item.callID) : Route.caseDetail(item.callID)) {
                        row(item)
                    }
                }
            } header: {
                HStack {
                    Text(month)
                    Spacer()
                    Text(Format.count(list.count, (one: "звонок", few: "звонка", many: "звонков")))
                }
            }
        }
    }

    private var groups: [(String, [HistoryItem])] {
        var out: [(String, [HistoryItem])] = []
        for item in items {
            let m = Format.month(item.startedAt)
            if out.last?.0 == m { out[out.count - 1].1.append(item) } else { out.append((m, [item])) }
        }
        return out
    }

    private func row(_ c: HistoryItem) -> some View {
        let scheme = c.schemeID.flatMap { model.schemesByID[$0] }
        return HStack(alignment: .top, spacing: 10) {
            Dot(color: c.schemeID != nil ? .red : c.noReport ? .secondary : .green).padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(Format.phone(c.phone) ?? "номер неизвестен").font(.subheadline.monospacedDigit())
                    Spacer()
                    Text(Format.when(c.startedAt)).font(.caption).foregroundStyle(.secondary)
                }
                HStack(spacing: 6) {
                    Text(c.title(schemeName: scheme?.name)).font(.headline)
                    if c.replay { Chip("запись") }
                }
                if let stage = c.maxStage, let scheme {
                    Text("этап \(stage) из \(scheme.stages.count)").font(.caption).foregroundStyle(.secondary)
                }
                if let legend = c.legend, c.schemeID != nil {
                    Text(legend).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Поиск по номеру: что знает база (NumberPage в History.jsx).
struct NumberView: View {
    @Environment(AppModel.self) private var model
    let phone: String
    @State private var data: NumberHistory?
    @State private var error: String?

    var body: some View {
        List {
            if let error { ErrorText(text: error) }
            if let info = data?.info {
                Section {
                    Text(Format.phone(info.phone) ?? info.phone).font(.title2.monospacedDigit().weight(.semibold))
                    if info.callsCount == 0 {
                        Text("С этого номера не звонили. Если позвонит — агент проверит его в начале звонка.").foregroundStyle(.secondary)
                    } else {
                        PropRow(label: "Звонков") { Text("\(info.callsCount)") }
                        PropRow(label: "Первый") { Text(Format.when(info.firstSeen)) }
                        PropRow(label: "Последний") { Text(Format.when(info.lastSeen)) }
                        if !info.tags.isEmpty {
                            VStack(alignment: .leading) {
                                Text("Метки").foregroundStyle(.secondary)
                                FlowLayout {
                                    ForEach(info.tags, id: \.self) { Chip(Format.tag($0), tone: $0 == "fraud" ? .red : .neutral) }
                                }
                            }
                        }
                        if !info.schemes.isEmpty {
                            VStack(alignment: .leading) {
                                Text("Схемы").foregroundStyle(.secondary)
                                ForEach(info.schemes, id: \.self) { id in
                                    NavigationLink(model.schemesByID[id]?.name ?? id, value: Route.scheme(id))
                                }
                            }
                        }
                    }
                }
                HistoryRows(items: (data?.cases ?? []).map { HistoryItem(case: $0) })
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle(Format.phone(phone) ?? phone)
        .inlineTitle()
        .task(id: phone) {
            do {
                data = try await model.client?.number(phone)
                error = nil
            } catch {
                self.error = CallSession.message(error)
            }
        }
    }
}

/// Кейс из истории: если лог звонка есть на сервере — полный отчёт со стенограммой, иначе — то, что в базе.
struct CaseView: View {
    @Environment(AppModel.self) private var model
    let callID: String
    @State private var session: CallSession?
    @State private var fallback: CallCase?
    @State private var fallbackLoaded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let session, let summary = session.state.summary {
                    let scheme = summary.schemeID.flatMap { model.schemesByID[$0] }
                    ReportView(
                        state: session.state, callCase: summary,
                        stages: session.state.stages.isEmpty ? scheme?.stages ?? [] : session.state.stages,
                        schemeName: scheme?.name, replay: session.state.summaryFromOtherCall
                    )
                } else if let fallback {
                    let scheme = fallback.schemeID.flatMap { model.schemesByID[$0] }
                    Banner(text: "Лога этого звонка нет на сервере — показано то, что агент сохранил в базу.")
                    ReportView(state: nil, callCase: fallback, stages: scheme?.stages ?? [], schemeName: scheme?.name)
                } else if fallbackLoaded || (session?.streamDone == true && session?.state.summary == nil && !(session?.missing ?? false)) {
                    Text("Кейса \(callID) нет ни в логах, ни в базе.").foregroundStyle(.secondary)
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding()
        }
        .navigationTitle(Format.phone(session?.state.summary?.phone ?? fallback?.phone) ?? callID)
        .inlineTitle()
        .task(id: callID) {
            guard session == nil, let client = model.client else { return }
            let s = CallSession(client: client, kind: .viewer)
            s.attach(callID: callID)
            session = s
        }
        .task(id: session?.missing) {
            guard session?.missing == true, fallback == nil else { return }
            let cases = (try? await model.client?.cases(limit: 500)) ?? []
            fallback = cases.first { $0.callID == callID }
            fallbackLoaded = true
        }
    }
}
