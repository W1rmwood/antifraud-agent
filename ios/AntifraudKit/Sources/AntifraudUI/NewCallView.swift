import AntifraudCore
import SwiftUI
import UniformTypeIdentifiers

/// Новый звонок: слушать микрофоном, тестовые звонки и записи с сервера, своя запись (NewCall.jsx).
/// Источников сервера mic и loopback здесь нет: телефон слушает сам и шлёт звук (remote).
struct NewCallView: View {
    @Environment(AppModel.self) private var model
    @State private var phone = ""
    @State private var phoneError: String?
    @State private var starting = false
    @State private var startError: String?
    @State private var sources: Sources?
    @State private var sourcesError: String?
    @State private var speed: Double = 1
    @State private var importing = false
    @State private var uploading = false
    @State private var uploaded: UploadResult?
    @State private var uploadError: String?
    @State private var realtime = true

    var body: some View {
        @Bindable var model = model
        List {
            liveSection
            if let busy = sources?.busy, model.ownSession(for: busy) == nil {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Уже идёт звонок: модель одна, второй придётся подождать.")
                        Button("Открыть текущий") { model.openCall(busy) }
                    }
                }
            }
            Section {
                Toggle(isOn: $model.showProvoke) {
                    VStack(alignment: .leading) {
                        Text("Грубые подсказки")
                        Text("Режим провокации: абсурд, издёвка, бывает мат").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            uploadSection
            testsSection
            demoSection
            flowSection
        }
        .navigationTitle("Новый звонок")
        .refreshable { await load() }
        .task(id: model.config) { await load() }
        .fileImporter(isPresented: $importing, allowedContentTypes: Self.uploadTypes) { result in
            Task { await upload(result) }
        }
    }

    // MARK: - Слушать микрофоном

    private var liveSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 10) {
                Text("Агент слушает разговор и подсказывает по ходу").font(.headline)
                Text("Положите этот телефон рядом с тем, на котором идёт разговор, и включите там громкую связь.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Номер звонящего")
                    Text("необязательно").font(.caption).foregroundStyle(.secondary)
                }
                TextField("+7 999 000-00-02", text: $phone)
                    .phoneInput()
                    .font(.body.monospacedDigit())
                    .accessibilityLabel("Номер звонящего, необязательно")
                Text("Агент сразу проверит, звонил ли этот номер раньше и с какой схемой.")
                    .font(.caption).foregroundStyle(.secondary)
                if let phoneError { Text(phoneError).font(.caption).foregroundStyle(.red) }
            }
            NeedView(missing: model.health?.blockers(asr: true) ?? [])
            Button {
                Task { await startLive() }
            } label: {
                Label(starting ? "Запускаю агента…" : "Слушать микрофоном", systemImage: "mic.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
            .disabled(starting || model.client == nil || !(model.health?.blockers(asr: true).isEmpty ?? true) || sources?.busy != nil)
            if let startError { Text(startError).font(.subheadline).foregroundStyle(.red) }
        } footer: {
            Text("iOS не даёт приложениям слушать сотовый звонок на этом же iPhone, поэтому разговор идёт на другом телефоне.")
        }
    }

    private func startLive() async {
        let normalized: String?
        do {
            normalized = try PhoneNumber.normalize(phone)
            phoneError = nil
        } catch {
            phoneError = error.errorDescription
            return
        }
        await start(.remote(phone: normalized))
    }

    private func start(_ request: StartCallRequest) async {
        starting = true
        startError = await model.startCall(request)
        starting = false
        if startError != nil { await load() }
    }

    // MARK: - Своя запись

    static let uploadTypes: [UTType] = [.wav, .mp3, .plainText, .audio] + ["flac", "ogg"].compactMap { UTType(filenameExtension: $0) }

    private var uploadSection: some View {
        Section {
            Button {
                importing = true
            } label: {
                Label(uploaded.map { "Выбрана: \($0.name)" } ?? "Выбрать файл", systemImage: "square.and.arrow.up")
            }
            .disabled(uploading || model.client == nil)
            if uploading { ProgressView("Загружаю…") }
            if let uploadError { Text(uploadError).font(.subheadline).foregroundStyle(.red) }
            if let uploaded {
                Picker("Темп разбора", selection: $realtime) {
                    Text("Как звонок").tag(true)
                    Text("Быстро").tag(false)
                }
                .pickerStyle(.segmented)
                Text(realtime ? "Реплики идут с паузами, как в настоящем звонке — для показа" : "Агент разбирает запись так быстро, как успевает модель")
                    .font(.caption).foregroundStyle(.secondary)
                NeedView(missing: model.health?.blockers() ?? [])
                Button(starting ? "Запускаю агента…" : "Разобрать запись") {
                    Task { await start(.file(path: uploaded.path, phone: try? PhoneNumber.normalize(phone), realtime: realtime)) }
                }
                .disabled(starting || sources?.busy != nil || !(model.health?.blockers().isEmpty ?? true))
            }
        } header: {
            Text("Загрузить запись")
        } footer: {
            Text("Аудио: WAV, FLAC, OGG, MP3 — в стерео левый канал звонящий, правый вы. Расшифровка: .txt, по реплике на строку: «caller: …», «user: …».")
        }
    }

    private func upload(_ result: Result<URL, Error>) async {
        uploadError = nil
        guard case .success(let url) = result else {
            if case .failure(let e) = result { uploadError = e.localizedDescription }
            return
        }
        guard let client = model.client else { return }
        uploading = true
        defer { uploading = false }
        // Файл из «Файлов» — за пределами песочницы: доступ открывается на время загрузки.
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            uploaded = try await client.upload(name: url.lastPathComponent, fileURL: url)
        } catch {
            uploaded = nil
            uploadError = CallSession.message(error)
        }
    }

    // MARK: - Тестовые звонки и записи

    @ViewBuilder
    private var testsSection: some View {
        if let sourcesError {
            Section { ErrorText(text: "Не удалось получить список звонков: \(sourcesError)") }
        }
        let audio = sources?.calls.filter { $0.kind == "audio" } ?? []
        let texts = sources?.calls.filter { $0.kind == "transcript" } ?? []
        if !audio.isEmpty {
            Section {
                ForEach(audio) { c in
                    callRow(
                        title: (c.path as NSString).lastPathComponent,
                        meta: c.schemeID.map { model.schemesByID[$0]?.name ?? $0 } ?? "без мошенничества",
                        phone: Format.phone(c.phone),
                        missing: model.health?.blockers(lfs: true) ?? []
                    ) { await start(.file(path: c.path)) }
                }
            } header: {
                Text("Записи из репозитория")
            } footer: {
                Text("Синтезированные звонки по сценариям: агент разбирает их на сервере.")
            }
        }
        if !texts.isEmpty {
            Section {
                ForEach(texts) { c in
                    let parts = c.title.components(separatedBy: " — ")
                    callRow(
                        title: parts.count > 1 ? parts[1] : c.title, meta: (c.path as NSString).lastPathComponent, phone: nil,
                        missing: model.health?.blockers() ?? []
                    ) { await start(.file(path: c.path)) }
                }
            } header: {
                Text("Реальные разговоры с мошенниками")
            } footer: {
                Text("Расшифровки пранк-звонков с YouTube, текстом — без распознавания речи.")
            }
        }
    }

    @ViewBuilder
    private var demoSection: some View {
        if let records = sources?.records, !records.isEmpty {
            Section {
                Picker("Скорость", selection: $speed) {
                    ForEach([1.0, 2.0, 4.0], id: \.self) { Text("\(Int($0))×").tag($0) }
                }
                .pickerStyle(.segmented)
                ForEach(records) { r in
                    callRow(title: r.title, meta: (r.path as NSString).lastPathComponent, phone: nil, missing: [], icon: "play.circle") {
                        await start(.replay(path: r.path, speed: speed))
                    }
                }
            } header: {
                Text("Демо без модели")
            } footer: {
                Text("Проигрываются события агента с настоящими паузами. Ollama не нужна — запасной вариант для защиты.")
            }
        }
    }

    private func callRow(
        title: String, meta: String, phone: String?, missing: [HealthCheck], icon: String = "doc.text",
        action: @escaping () async -> Void
    ) -> some View {
        Button {
            Task { await action() }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: icon).foregroundStyle(.tint).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).foregroundStyle(.primary)
                    Text(meta).font(.caption).foregroundStyle(.secondary)
                    if !missing.isEmpty {
                        Text("Не хватает: \(missing.map(\.label).joined(separator: ", "))").font(.caption).foregroundStyle(.orange)
                    }
                }
                Spacer()
                if let phone { Text(phone).font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
            }
        }
        .disabled(starting || !missing.isEmpty || sources?.busy != nil)
    }

    private var flowSection: some View {
        Section("Как пойдёт звонок") {
            flowItem(1, "Фраза — на экране", "Агент распознаёт речь и показывает реплику примерно через 2 с после того, как её договорили.")
            flowItem(2, "Признак — плашка", "Правила без модели ставят красную плашку сразу за фразой: перевод, код из СМС, наличные, «никому не говорите».")
            flowItem(3, "Схема — подсказка", "Модель узнаёт схему обычно через 10–20 с после первого требования: этап, что будет дальше и что сказать.")
        }
    }

    private func flowItem(_ n: Int, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(n)").font(.headline).frame(width: 24, height: 24).background(Circle().fill(.quaternary))
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(text).font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func load() async {
        guard let client = model.client else { return }
        do {
            sources = try await client.sources()
            sourcesError = nil
        } catch {
            sourcesError = CallSession.message(error)
        }
    }
}

/// Чего не хватает на сервере для звонка через модель.
struct NeedView: View {
    var missing: [HealthCheck]

    var body: some View {
        if !missing.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Агенту нужна модель, а на сервере не хватает:").font(.subheadline)
                ForEach(missing) { c in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Dot(color: .orange)
                            Text("\(c.label): \(c.detail)").font(.subheadline)
                        }
                        FixCommand(command: c.fix)
                    }
                }
                Text("Пока модели нет, работу интерфейса можно показать на записях «Демо без модели».")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}
