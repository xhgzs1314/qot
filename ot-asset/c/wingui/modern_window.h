
#pragma once
#include "renderer.h"
#include "animation.h"
#include "ui_component.h"
#include "controls_basic.h"  
#include <vector>
#include <memory>
#include <chrono>
#include <string>

namespace gui {


class ModernWindow {
public:
    ModernWindow();
    ~ModernWindow();
    
    bool Initialize(HINSTANCE hInstance, int nCmdShow, 
                    const std::wstring& title = L"Modern GUI Window",
                    int width = 800, int height = 600);
    void Run();
    
    
    void AddComponent(std::unique_ptr<UIComponent> component);
    void RemoveComponent(int id);
    void ClearComponents();
    
    
    void SetBackgroundColor(const Color& color);
    void SetBackgroundGradient(const Color& start, const Color& end);
    void SetUseGradientBackground(bool use);
    void SetCornerRadius(float radius);
    
    
    void OnChar(wchar_t ch);
    void OnKeyDown(int vkey);
    void OnImeComposition(const std::wstring& compositionString);
    void OnImeStartComposition();
    void OnImeEndComposition();
    
    Renderer* GetRenderer() { return &m_renderer; }
    HWND GetHwnd() const { return m_hwnd; }
    void SetTitle(const std::wstring& title) { if (m_hwnd) SetWindowTextW(m_hwnd, title.c_str()); }
    void UpdateImePosition();
    
    void SetFocusedComponent(UIComponent* comp);
    UIComponent* GetFocusedComponent() const { return m_focusedComponent; }

    // 是否有动画在跑（WM_TIMER 用它决定是否请求下一帧）
    bool HasActiveAnimation() const { return AnimationManager::Instance().IsAnimating(); }
    // 请求重绘（外部改属性后调用，保证立刻看到变化）
    void RequestRepaint() { if (m_hwnd) InvalidateRect(m_hwnd, nullptr, FALSE); }
    
private:
    // 动画帧定时器 id（16ms ≈ 60fps；OnPaint 里发现无动画在跑就停表）
    static constexpr UINT_PTR kAnimTimerId = 0x514F;
    static void SetDpiAwarenessOnce();
    void OnPaint();
    void OnResize();
    void UpdateAnimation();
    void HandleMouse();
    void SetCompositionFont(HIMC hImc);
    void UpdateCompositionWindow();
    static LRESULT CALLBACK WindowProc(HWND hwnd, UINT uMsg, WPARAM wParam, LPARAM lParam);
    
    HWND m_hwnd;
    Renderer m_renderer;
    Size m_size;
    
    
    std::vector<std::unique_ptr<UIComponent>> m_components;
    
    
    Color m_bgColorStart;
    Color m_bgColorEnd;
    bool m_useGradientBg;
    float m_cornerRadius;
    
    
    std::chrono::steady_clock::time_point m_lastFrameTime;
    
    
    Point m_mousePos;
    UIComponent* m_focusedComponent = nullptr;
    
    
    std::wstring m_imeCompositionString;
    bool m_isImeComposing = false;
};

} 