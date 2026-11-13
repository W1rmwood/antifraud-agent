import AntifraudCore
import SwiftUI

/// Сервер: найденные в сети, ручной ввод адреса, код доступа, готовность к звонку.
struct ServerView: View {
    @Environment(AppModel.self) private var model
    @State private var browser = ServerBrowser()
    @State private var address = ""
    @State private var token = ""
    @State private var addressError: String?

    var body: some View {
        Form {
            Section {
                if browser.found.isEmpty {
                    HStack(spacing: 10) {
                        if browser.searching { ProgressView() }
                        Text(browser.searching ? "Ищу серверы в этой сети…" : "Поиск выключен").foregroundStyle(.secondary)
                    }
                }
                ForEach(browser.found) { s in
                    Button {
                        if let url = s.url { address = url.absoluteString }
                    } label: {
                        HStack {
                            Image(systemName: "desktopcomputer").accessibilityHidden(true)
                            VStack(alignment: .leading) {
                                Text(s.name).foregroundStyle(.primary)
                                Text(s.url?.absoluteString ?? "определяю адрес…").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if s.url?.absoluteString == address { Image(systemName: "checkmark").foregroundStyle(.tint) }
                        }
                    }
                    .disabled(s.url == nil)
                }
                if let e = browser.error { Text(e).font(.caption).foregroundStyle(.orange) }
            } header: {
                Text("Найденные в сети")
            } footer: {
                Text("Сервер запускают на компьютере: .venv/bin/python -m ui.server --host 0.0.0.0. Он печатает адрес и код для телефона.")
            }

            Section {
                TextField("192.168.1.5:8642", text: $address)
                    .plainInput()
                    .accessibilityLabel("Адрес сервера")
                SecureField("Код доступа: 6 цифр", text: $token)
                    .numberInput()
                    .accessibilityLabel("Код доступа")
                if let addressError { Text(addressError).font(.caption).foregroundStyle(.red) }
                Button {
                    Task { await connect() }
                } label: {
                    HStack {
                        Text("Проверить и сохранить")
                        if model.checking { Spacer(); ProgressView() }
                    }
                }
                .disabled(model.checking || address.trimmingCharacters(in: .whitespaces).isEmpty)
            } header: {
                Text("Адрес и код")
            } footer: {
                Text("Код нужен, если сервер запущен для локальной сети. Телефон и компьютер должны быть в одной сети Wi-Fi.")
            }

            if let check = model.check { resultSection(check) }

            if model.config != nil {
                Section {
                    Button("Обновить готовность") { Task { await model.refresh() } }.disabled(model.checking)
                    Button("Забыть сервер", role: .destructive) {
                        model.forgetServer()
                        address = ""
                        token = ""
                    }
                }
            }
        }
        .navigationTitle("Сервер")
        .onAppear {
            browser.start()
            if address.isEmpty, let config = model.config {
                address = config.baseURL.absoluteString
                token = config.token
            }
        }
        .onDisappear { browser.stop() }
        .task(id: model.config) { if model.check == nil { await model.refresh() } }
    }

    @ViewBuilder
    private func resultSection(_ check: ServerCheck) -> some View {
        Section("Проверка") {
            Label(check.message, systemImage: check.isOK ? "checkmark.circle.fill" : "xmark.octagon.fill")
                .foregroundStyle(check.isOK ? (model.health?.modelReady == true ? Color.green : Color.orange) : Color.red)
            if case .ok(_, let health) = check {
                HStack(spacing: 8) {
                    Dot(color: .green)
                    VStack(alignment: .leading) {
                        Text("Запись без модели")
                        Text("готова").font(.caption).foregroundStyle(.secondary)
                    }
                }
                ForEach(health.checks) { c in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Dot(color: c.ok ? .green : .secondary)
                            VStack(alignment: .leading) {
                                Text(c.label)
                                Text(c.detail).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        if !c.ok { FixCommand(command: c.fix) }
                    }
                    .accessibilityElement(children: .combine)
                }
                Text("Звонку с микрофона нужны зависимости, обе модели, индекс схем и распознавание речи.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func connect() async {
        guard let url = ServerConfig.parseAddress(address) else {
            addressError = "Не похоже на адрес: нужен вид 192.168.1.5:8642"
            return
        }
        addressError = nil
        let code = token.trimmingCharacters(in: .whitespaces)
        let result = await model.connect(ServerConfig(baseURL: url, token: code))
        if result.isOK {
            address = url.absoluteString
        }
    }
}
