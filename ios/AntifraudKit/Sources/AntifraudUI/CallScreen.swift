import AntifraudAudio
import AntifraudCore
import SwiftUI

/// Экран звонка: идёт сейчас, закончился или открыт из истории — одни и те же события (CallPage.jsx).
struct CallScreen: View {
    @Environment(AppModel.self) private var model
    let callID: String
    @State private var viewer: CallSession?

    var body: some View {
        Group {
            if let session = model.ownSession(for: callID) ?? viewer {
                CallContent(session: session)
            } else if model.client == nil {
                ContentUnavailableView("Сервер не выбран", systemImage: "network.slash")
            } else {
                ProgressView()
            }
        }
        .task(id: callID) {
            guard model.ownSession(for: callID) == nil, viewer?.callID != callID, let client = model.client else { return }
            let s = CallSession(client: client, kind: .viewer)
            s.attach(callID: callID)
            viewer = s
        }
    }
}

struct CallContent: View {
    @Environment(AppModel.self) private var model
    let session: CallSession
    @State private var tab = 0
    @State private var stickToBottom = true
    @State private var demoError: String?

    private var state: CallState { session.state }

    var body: some View {
        Group {
            if session.missing {
                ContentUnavailableView {
                    Label("Звонка нет", systemImage: "questionmark.folder")
                } description: {
                    Text("Звонка \(session.callID ?? "") нет: лог data/runs/\(session.callID ?? "").jsonl не найден.")
                }
            } else if session.deadOnStart {
                ScrollView {
                    VStack(spacing: 12) {
                        header
                        CrashCard(error: session.process?.error ?? "", started: false, onReplayDemo: replayDemo)
                        if let demoError { ErrorText(text: demoError) }
                    }
                    .padding()
                }
            } else {
                content
            }
        }
        .navigationTitle(Format.phone(state.phone) ?? "Номер неизвестен")
        .inlineTitle()
        .toolbar {
            if state.summary != nil {
                ToolbarItem(placement: .principal) {
                    Picker("Вид", selection: $tab) {
                        Text("Разговор").tag(0)
                        Text("Отчёт").tag(1)
                    }
                    .pickerStyle(.segmented)
                    .fixedSize()
                }
            }
        }
        .safeAreaInset(edge: .bottom) {
            if session.canStop {
                Button(role: .destructive) {
                    Task { await session.stop() }
                } label: {
                    Label(session.isStopping ? "Агент дописывает отчёт…" : "Закончить", systemImage: "stop.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .controlSize(.large)
                .disabled(session.isStopping)
                .padding(.horizontal)
                .padding(.bottom, 8)
                .background(.bar)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if tab == 1, let summary = state.summary {
            ScrollView {
                ReportView(state: state, callCase: summary, stages: state.stages, replay: session.replay)
                    .padding()
            }
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 12) {
                        header
                        banners
                        if session.crashed {
                            CrashCard(error: session.process?.error ?? "", started: true, onReplayDemo: nil)
                        }
                        VerdictCard(state: state, live: session.live)
                        if let hangup = state.hangup { HangupCard(text: hangup.text) }
                        HintCard(hints: state.hints, showProvoke: model.showProvoke)
                        SignsCard(warnings: state.warnings) { seq in
                            stickToBottom = false
                            withAnimation { proxy.scrollTo(seq, anchor: .center) }
                        }
                        SchemeProgressCard(state: state, stages: state.stages)
                        transcript(proxy)
                        AgentLogCard(log: state.log, replay: session.replay)
                    }
                    .padding()
                }
            }
        }
    }

    // MARK: - Шапка

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                statusLabel
                Spacer()
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let now = session.live ? context.date : nil
                    Text(Format.clock(state.clock(at: state.endedTs ?? now ?? state.lastTs ?? Date())))
                        .font(.body.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            FlowLayout {
                let n = state.numberInfo?.callsCount ?? 0
                if n > 0 { Chip("звонил уже \(Format.count(n, (one: "раз", few: "раза", many: "раз")))", tone: .red) }
                if let source = Format.source(state.source, replay: session.replay) {
                    Chip(source, systemImage: session.replay ? "play.fill" : sourceIcon)
                }
            }
            if session.kind == .remote, session.phase == .live || session.phase == .stopping || session.phase == .starting {
                RecordingStatus(mic: session.mic, level: session.level, upload: session.upload)
            }
        }
    }

    private var sourceIcon: String {
        switch state.source?.kind {
        case "mic", "remote": "mic.fill"
        case "loopback": "speaker.wave.2.fill"
        default: "doc"
        }
    }

    private var statusLabel: some View {
        let live = session.live
        let text = live ? (session.isStopping ? "Завершаю" : "Идёт")
            : state.ended ? "Закончен" : session.crashed ? "Агент упал" : session.streamDone ? "Оборвался" : "Загрузка"
        return HStack(spacing: 6) {
            if live { Dot(color: .red) }
            Text(text).font(.headline)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Звонок: \(text)")
    }

    @ViewBuilder
    private var banners: some View {
        if let e = session.stopError { Banner(text: e, tone: .red, systemImage: "exclamationmark.triangle") }
        if session.unauthorized {
            Banner(text: "Сервер не принял код доступа. Проверьте его на вкладке «Сервер».", tone: .red, systemImage: "lock")
        }
        if session.reconnecting, !session.streamDone {
            Banner(text: "Нет связи с сервером — переподключаюсь…", tone: .orange, systemImage: "wifi.exclamationmark")
        }
        if let e = session.streamError { Banner(text: e, tone: .red, systemImage: "exclamationmark.triangle") }
        if session.streamDone, !state.ended, !session.crashed, !state.lines.isEmpty {
            Banner(text: "Лог обрывается: звонок не закончился штатно, отчёта нет.")
        }
        if state.summary != nil, !session.live {
            VStack(alignment: .leading, spacing: 6) {
                Banner(
                    text: session.replay ? "Запись закончилась, отчёт — из записанного звонка." : "Звонок закончен, кейс сохранён в базу.",
                    tone: .green, systemImage: "checkmark.circle"
                )
                Button("Открыть отчёт") { tab = 1 }.font(.subheadline)
            }
        }
    }

    // MARK: - Расшифровка

    private func transcript(_ proxy: ScrollViewProxy) -> some View {
        Card("Разговор", systemImage: "waveform") {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    if state.lines.isEmpty {
                        if session.live {
                            ListeningView(text: "Ждём первую фразу. Агент слушает звонок.")
                        } else {
                            Text("В этом звонке не было ни одной фразы.").foregroundStyle(.secondary)
                        }
                    }
                    TranscriptItems(lines: state.lines, notes: state.inline)
                    // Метка конца: видна — значит, человек внизу ленты и новые реплики прокручиваются сами.
                    Color.clear.frame(height: 1).id("bottom")
                        .onAppear { stickToBottom = true }
                        .onDisappear { stickToBottom = false }
                }
            }
            .frame(minHeight: 200, maxHeight: 380)
            .onChange(of: state.lines.count) {
                guard session.live, stickToBottom else { return }
                withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
            }
        }
    }

    private func replayDemo() {
        Task {
            demoError = await model.startCall(.replay(path: "data/runs/demo.jsonl"))
        }
    }
}

/// Индикатор записи и отправки звука.
struct RecordingStatus: View {
    var mic: MicState
    var level: Float
    var upload: AudioUploader.Status?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            switch mic {
            case .running:
                HStack(spacing: 8) {
                    Dot(color: .red)
                    Text("Слушаю микрофоном").font(.subheadline.weight(.semibold))
                    ProgressView(value: Double(min(level * 4, 1)))
                        .tint(.red)
                        .frame(maxWidth: 120)
                        .accessibilityLabel("Громкость")
                }
            case .interrupted:
                Banner(
                    text: "Запись прервана системой — например, звонком на этот iPhone. Продолжится сама, когда прерывание закончится.",
                    tone: .orange, systemImage: "mic.slash"
                )
            case .denied:
                Banner(text: "Нет доступа к микрофону. Разрешите его: Настройки → Антимошенник → Микрофон.", tone: .red, systemImage: "mic.slash")
            case .failed(let text):
                Banner(text: text, tone: .red, systemImage: "mic.slash")
            case .idle:
                Label("Микрофон выключен", systemImage: "mic.slash").font(.subheadline).foregroundStyle(.secondary)
            }
            if let upload { uploadLine(upload) }
        }
    }

    @ViewBuilder
    private func uploadLine(_ s: AudioUploader.Status) -> some View {
        switch s.state {
        case .retrying(let reason):
            Label(
                "Нет связи с сервером, повторяю (\(reason)). В очереди \(Format.seconds(s.queuedSeconds)) звука.",
                systemImage: "wifi.exclamationmark"
            )
            .font(.caption).foregroundStyle(.orange)
        case .stopped(.callEnded):
            Label("Сервер закончил звонок — звук больше не отправляется", systemImage: "checkmark.circle")
                .font(.caption).foregroundStyle(.secondary)
        case .stopped(.unauthorized):
            Label("Сервер не принял код доступа — звук не отправляется", systemImage: "lock").font(.caption).foregroundStyle(.red)
        case .idle, .sending, .stopped(.cancelled):
            Label("Звук уходит на сервер", systemImage: "arrow.up.circle").font(.caption).foregroundStyle(.secondary)
        }
        if s.lostSeconds > 0 {
            Label("Потеряно \(Format.seconds(s.lostSeconds)) звука: сеть пропадала надолго", systemImage: "exclamationmark.triangle")
                .font(.caption).foregroundStyle(.orange)
        }
    }
}
