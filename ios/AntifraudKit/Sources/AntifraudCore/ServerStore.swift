import Foundation

/// Адрес сервера и код доступа в UserDefaults.
/// Код — шестизначный код тестового сервера в локальной сети, а не секрет уровня Keychain.
public struct ServerStore: Sendable {
    /// nil — стандартные настройки приложения; в тестах — отдельный набор.
    public let suiteName: String?

    static let addressKey = "server.address"
    static let tokenKey = "server.token"
    static let showProvokeKey = "ui.showProvoke"

    public init(suiteName: String? = nil) {
        self.suiteName = suiteName
    }

    private var defaults: UserDefaults {
        suiteName.flatMap { UserDefaults(suiteName: $0) } ?? .standard
    }

    /// Сохранённый сервер; nil — первый запуск.
    public func load() -> ServerConfig? {
        guard let raw = defaults.string(forKey: Self.addressKey), let url = URL(string: raw) else { return nil }
        return ServerConfig(baseURL: url, token: defaults.string(forKey: Self.tokenKey) ?? "")
    }

    public func save(_ config: ServerConfig) {
        defaults.set(config.baseURL.absoluteString, forKey: Self.addressKey)
        defaults.set(config.token, forKey: Self.tokenKey)
    }

    public func clear() {
        defaults.removeObject(forKey: Self.addressKey)
        defaults.removeObject(forKey: Self.tokenKey)
    }

    /// Показывать грубые подсказки (режим провокации), как переключатель в вебе.
    public var showProvoke: Bool {
        get { defaults.object(forKey: Self.showProvokeKey) as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: Self.showProvokeKey) }
    }
}
