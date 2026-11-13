import Foundation
import Network
import Observation

/// Серверы «Антимошенника» в локальной сети: Bonjour `_antifraud._tcp` (сервер объявляет себя через dns-sd).
/// Имя службы превращается в адрес пробным соединением: из него берётся IPv4 и порт.
@MainActor
@Observable
public final class ServerBrowser {
    public struct Found: Identifiable, Hashable, Sendable {
        public var name: String
        /// nil — адрес ещё определяется.
        public var url: URL?
        public var id: String { name }
    }

    public static let serviceType = "_antifraud._tcp"

    public private(set) var found: [Found] = []
    public private(set) var error: String?
    public private(set) var searching = false

    private var browser: NWBrowser?
    private var connections: [String: NWConnection] = [:]

    public init() {}

    public func start() {
        guard browser == nil else { return }
        let params = NWParameters()
        params.includePeerToPeer = false
        let b = NWBrowser(for: .bonjour(type: Self.serviceType, domain: nil), using: params)
        b.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in self?.handle(state) }
        }
        b.browseResultsChangedHandler = { [weak self] results, _ in
            let services: [(String, NWEndpoint)] = results.compactMap { r in
                if case .service(let name, _, _, _) = r.endpoint { return (name, r.endpoint) }
                return nil
            }
            Task { @MainActor in self?.update(services) }
        }
        browser = b
        searching = true
        b.start(queue: .main)
    }

    public func stop() {
        browser?.cancel()
        browser = nil
        for c in connections.values { c.cancel() }
        connections.removeAll()
        searching = false
    }

    private func handle(_ state: NWBrowser.State) {
        switch state {
        case .failed(let e):
            error = "Поиск в сети не работает: \(e.localizedDescription)"
            searching = false
        case .waiting(let e):
            // Чаще всего — нет разрешения «Локальная сеть» (Настройки → Антимошенник).
            error = "Поиск в сети ждёт: \(e.localizedDescription). Проверьте разрешение «Локальная сеть»."
        case .ready:
            error = nil
        default:
            break
        }
    }

    private func update(_ services: [(String, NWEndpoint)]) {
        let names = Set(services.map(\.0))
        found.removeAll { !names.contains($0.name) }
        for (name, endpoint) in services where !found.contains(where: { $0.name == name }) {
            found.append(Found(name: name, url: nil))
            resolve(name, endpoint)
        }
    }

    private func resolve(_ name: String, _ endpoint: NWEndpoint) {
        let params = NWParameters.tcp
        if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
            ip.version = .v4
        }
        let connection = NWConnection(to: endpoint, using: params)
        connections[name] = connection
        connection.stateUpdateHandler = { [weak self, weak connection] state in
            switch state {
            case .ready:
                let url = connection?.currentPath?.remoteEndpoint.flatMap(Self.url(for:))
                connection?.cancel()
                Task { @MainActor in self?.resolved(name, url) }
            case .failed, .cancelled:
                Task { @MainActor in self?.connections[name] = nil }
            default:
                break
            }
        }
        connection.start(queue: .main)
    }

    private func resolved(_ name: String, _ url: URL?) {
        connections[name] = nil
        if let i = found.firstIndex(where: { $0.name == name }) { found[i].url = url }
    }

    /// Адрес службы → http://192.168.1.5:8642.
    nonisolated static func url(for endpoint: NWEndpoint) -> URL? {
        guard case .hostPort(let host, let port) = endpoint else { return nil }
        let text: String
        switch host {
        case .ipv4(let a):
            // У адреса службы в описании есть зона интерфейса («172.20.10.2%en0») — с ней URL не строится.
            text = String(String(describing: a).split(separator: "%").first ?? "")
        case .ipv6(let a):
            // Зона интерфейса («%en0») в URL не нужна: адрес и так из локальной сети.
            text = "[\(String(describing: a).split(separator: "%").first ?? "")]"
        case .name(let n, _):
            text = n
        @unknown default:
            return nil
        }
        return URL(string: "http://\(text):\(port.rawValue)")
    }
}
