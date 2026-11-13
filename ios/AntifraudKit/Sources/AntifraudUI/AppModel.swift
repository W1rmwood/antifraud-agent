import AntifraudCore
import Foundation
import Observation

/// Куда можно перейти внутри вкладки.
public enum Route: Hashable, Sendable {
    case call(String)
    case caseDetail(String)
    case number(String)
    case scheme(String)
}

/// Итог проверки сервера: понятная причина, а не код ошибки.
public enum ServerCheck: Equatable, Sendable {
    case ok(ServerInfo, Health)
    /// Сервер не ответил: адрес, сеть, не запущен.
    case notFound(String)
    /// Отвечает, но не «Антимошенник».
    case notOurs
    /// Наш, но старый: нет /api/info — телефон он не поддерживает.
    case outdated
    /// Наш, другая версия API.
    case apiMismatch(Int)
    /// Нужен код доступа, а его нет.
    case needCode
    /// Код не подошёл.
    case badCode(String)
    case failed(String)

    public var isOK: Bool { if case .ok = self { true } else { false } }

    public var message: String {
        switch self {
        case .ok(_, let health):
            health.modelReady ? "Сервер готов к звонку через модель" : "Сервер на связи, но модель не готова — работают только записи"
        case .notFound(let reason): "Сервер не найден. \(reason)"
        case .notOurs: "По этому адресу отвечает не «Антимошенник»."
        case .outdated: "Это «Антимошенник», но старой версии: он не принимает звук с телефона. Обновите проект на компьютере."
        case .apiMismatch(let v):
            "Версия API сервера — \(v), приложение знает версию \(ServerInfo.supportedAPI). Обновите приложение или сервер."
        case .needCode: "Сервер просит код доступа: шесть цифр, он напечатан в консоли сервера рядом с адресом."
        case .badCode(let m): "Неверный код доступа (\(m)). Код напечатан в консоли сервера рядом с адресом."
        case .failed(let m): m
        }
    }
}

/// Состояние приложения: сервер, вкладки, идущий звонок.
@MainActor
@Observable
public final class AppModel {
    public enum Tab: Hashable, Sendable { case call, history, schemes, server }

    public var tab: Tab
    public var callPath: [Route] = []
    public var historyPath: [Route] = []
    public var schemesPath: [Route] = []

    public private(set) var config: ServerConfig?
    public private(set) var check: ServerCheck?
    public private(set) var checking = false
    public private(set) var schemes: [SchemeBrief] = []
    /// Звонок, запущенный с этого телефона.
    public private(set) var session: CallSession?

    public var showProvoke: Bool {
        didSet { store.showProvoke = showProvoke }
    }

    private let store: ServerStore

    public init(store: ServerStore = ServerStore()) {
        self.store = store
        let saved = store.load()
        config = saved
        showProvoke = store.showProvoke
        // Первый запуск без сохранённого сервера открывает экран сервера.
        tab = saved == nil ? .server : .call
    }

    public var client: APIClient? { config.map { APIClient(config: $0) } }

    public var health: Health? {
        if case .ok(_, let h) = check { return h }
        return nil
    }

    public var schemesByID: [String: SchemeBrief] {
        Dictionary(schemes.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
    }

    // MARK: - Сервер

    /// Проверить адрес и код: /api/info, затем /api/health.
    public static func checkServer(_ config: ServerConfig) async -> ServerCheck {
        let client = APIClient(config: config)
        let info: ServerInfo
        do {
            info = try await client.info()
        } catch let e as APIError {
            switch e {
            case .network(let m): return .notFound(m)
            case .server(404, _): return .outdated
            case .server, .decoding: return .notOurs
            case .unauthorized(let m): return config.token.isEmpty ? .needCode : .badCode(m)
            case .invalidURL(let s): return .failed("Неверный адрес: \(s)")
            }
        } catch {
            return .failed(CallSession.message(error))
        }
        guard info.isAntifraud else { return .notOurs }
        guard info.api == ServerInfo.supportedAPI else { return .apiMismatch(info.api) }
        do {
            return .ok(info, try await client.health())
        } catch let e as APIError {
            if case .unauthorized(let m) = e { return config.token.isEmpty ? .needCode : .badCode(m) }
            if case .network(let m) = e { return .notFound(m) }
            return .failed(e.localizedDescription)
        } catch {
            return .failed(CallSession.message(error))
        }
    }

    /// Проверить и, если сервер наш и код подошёл, сохранить.
    @discardableResult
    public func connect(_ config: ServerConfig) async -> ServerCheck {
        checking = true
        defer { checking = false }
        let result = await Self.checkServer(config)
        check = result
        if result.isOK {
            self.config = config
            store.save(config)
            await loadSchemes()
        }
        return result
    }

    /// Обновить готовность сохранённого сервера.
    public func refresh() async {
        guard let config else { return }
        checking = true
        defer { checking = false }
        check = await Self.checkServer(config)
        if schemes.isEmpty, check?.isOK == true { await loadSchemes() }
    }

    public func forgetServer() {
        store.clear()
        config = nil
        check = nil
        tab = .server
    }

    private func loadSchemes() async {
        if let list = try? await client?.schemes() { schemes = list }
    }

    // MARK: - Звонки

    /// Запустить звонок и открыть его экран. Возвращает текст ошибки, если не вышло.
    public func startCall(_ request: StartCallRequest) async -> String? {
        guard let client else { return "Сначала выберите сервер" }
        if let s = session, s.live { return "Уже идёт звонок с этого телефона" }
        let s = CallSession(client: client, kind: request.source == "remote" ? .remote : .server)
        session = s
        await s.start(request)
        if case .failed(let message) = s.phase {
            session = nil
            if s.unauthorized { check = .badCode(message) }
            return message
        }
        if let id = s.callID {
            tab = .call
            callPath = [.call(id)]
        }
        return nil
    }

    /// Открыть звонок: свой идущий — тот же сеанс, чужой — только поток событий.
    public func openCall(_ callID: String, in tab: Tab = .call) {
        self.tab = tab
        switch tab {
        case .history: historyPath.append(.call(callID))
        default: callPath = [.call(callID)]
        }
    }

    /// Сеанс для экрана звонка: свой, если это он.
    public func ownSession(for callID: String) -> CallSession? {
        session?.callID == callID ? session : nil
    }
}
