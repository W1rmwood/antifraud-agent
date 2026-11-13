import Foundation
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

/// То, что есть только на iPhone: вибрация, экран без автоблокировки, буфер обмена.
/// На macOS (сборка пакета без Xcode) — заглушки.
@MainActor
enum Platform {
    /// Вибрация «внимание»: новая плашка или объявленная схема.
    static func warningHaptic() {
        #if canImport(UIKit)
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
        #endif
    }

    /// Пока идёт живой звонок, экран не гаснет.
    static func keepScreenOn(_ on: Bool) {
        #if canImport(UIKit)
        UIApplication.shared.isIdleTimerDisabled = on
        #endif
    }

    static func copy(_ text: String) {
        #if canImport(UIKit)
        UIPasteboard.general.string = text
        #elseif canImport(AppKit)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        #endif
    }
}

#if canImport(UIKit)
/// Клавиатура убирается нажатием в любое место экрана вне поля ввода.
/// Распознаватель висит на окне и нажатия не перехватывает: кнопки и списки под ним работают как обычно.
@MainActor
final class KeyboardDismisser: NSObject, UIGestureRecognizerDelegate {
    static let shared = KeyboardDismisser()
    private let windows = NSHashTable<UIWindow>.weakObjects()
    /// Поле, в котором был курсор в момент касания.
    private weak var focusedAtTouch: UIResponder?

    /// Вешает распознаватель на каждое окно приложения, на котором его ещё нет.
    /// Вызывается повторно: при первом показе экрана окно может быть ещё не готово.
    func install() {
        let all = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
        for window in all where !windows.contains(window) {
            let tap = UITapGestureRecognizer(target: self, action: #selector(tapped(_:)))
            tap.cancelsTouchesInView = false
            tap.delegate = self
            window.addGestureRecognizer(tap)
            windows.add(window)
        }
    }

    @objc private func tapped(_ recognizer: UITapGestureRecognizer) {
        guard let before = focusedAtTouch, let window = recognizer.view else { return }
        let point = recognizer.location(in: window)
        // Нажатие по самому полю (переставить курсор) клавиатуру не убирает.
        if let field = before as? UIView, field.convert(field.bounds, to: window).contains(point) { return }
        // Полю, по которому нажали, нужно время забрать курсор: если он перешёл в другое поле — это не «мимо».
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            guard UIResponder.currentFirst === before else { return }
            before.resignFirstResponder()
        }
    }

    func gestureRecognizer(_ recognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
        focusedAtTouch = UIResponder.currentFirst
        return focusedAtTouch != nil
    }

    func gestureRecognizer(_ recognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
        true
    }
}

private extension UIResponder {
    private static weak var found: UIResponder?

    /// Тот, у кого сейчас клавиатура: действие без адресата UIKit доставляет именно ему.
    static var currentFirst: UIResponder? {
        found = nil
        UIApplication.shared.sendAction(#selector(reportAsFirst), to: nil, from: nil, for: nil)
        return found
    }

    @objc private func reportAsFirst() {
        UIResponder.found = self
    }
}
#endif

extension Platform {
    /// Включает скрытие клавиатуры по нажатию мимо поля; вызывается один раз с корневого экрана.
    static func dismissKeyboardOnTap() {
        #if canImport(UIKit)
        KeyboardDismisser.shared.install()
        #endif
    }
}
