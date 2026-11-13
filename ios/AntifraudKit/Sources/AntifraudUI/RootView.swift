import AntifraudCore
import SwiftUI

/// Корневой экран: четыре вкладки — Звонок, История, Схемы, Сервер.
public struct RootView: View {
    @State private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase

    public init(model: AppModel? = nil) {
        _model = State(initialValue: model ?? AppModel())
    }

    public var body: some View {
        @Bindable var model = model
        TabView(selection: $model.tab) {
            NavigationStack(path: $model.callPath) {
                NewCallView().withRoutes()
            }
            .tabItem { Label("Звонок", systemImage: "phone.fill") }
            .tag(AppModel.Tab.call)

            NavigationStack(path: $model.historyPath) {
                HistoryView().withRoutes()
            }
            .tabItem { Label("История", systemImage: "clock.arrow.circlepath") }
            .tag(AppModel.Tab.history)

            NavigationStack(path: $model.schemesPath) {
                SchemesView().withRoutes()
            }
            .tabItem { Label("Схемы", systemImage: "book.closed") }
            .tag(AppModel.Tab.schemes)

            NavigationStack {
                ServerView()
            }
            .tabItem { Label("Сервер", systemImage: model.check?.isOK == false ? "exclamationmark.icloud" : "desktopcomputer") }
            .tag(AppModel.Tab.server)
        }
        .environment(model)
        .scrollDismissesKeyboard(.interactively)
        .onAppear { Platform.dismissKeyboardOnTap() }
        .onChange(of: scenePhase) { Platform.dismissKeyboardOnTap() }
        .task { await model.refresh() }
    }
}

extension View {
    /// Переходы, общие для всех вкладок.
    func withRoutes() -> some View {
        navigationDestination(for: Route.self) { route in
            switch route {
            case .call(let id): CallScreen(callID: id)
            case .caseDetail(let id): CaseView(callID: id)
            case .number(let phone): NumberView(phone: phone)
            case .scheme(let id): SchemeDetailView(id: id)
            }
        }
    }
}
