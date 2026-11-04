
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include "visual_template.h"

namespace gui {


class ProgressBar : public UIComponent {
public:
    ProgressBar();
    ~ProgressBar();
    
    void SetValue(float value);  
    void SetTargetValue(float value, float duration = 0.3f);
    float GetValue() const { return m_value; }
    
    void SetBackgroundColor(const Color& color);
    void SetProgressColor(const Color& color);
    void SetProgressGradient(const Color& start, const Color& end);
    void SetBorderRadius(float radius);
    void SetShowPercentage(bool show);
    void SetHeight(float height);
    
    void Draw(Renderer* renderer) override;
    void Update(float deltaTime) override;

    // v4：无交互态，直接用基类（disabled 位）

    // ---- v6：模板通道（主值 = 进度 0..1，动画值实时驱动填充条）----
    float TplValue(int src) const override {
        (void)src;
        return m_value;
    }
    
private:
    float m_value;
    float m_targetValue;
    Color m_backgroundColor;
    Color m_progressColorStart;
    Color m_progressColorEnd;
    float m_borderRadius;
    float m_height;
    bool m_showPercentage;
    bool m_useGradient;
    
    std::unique_ptr<Animation> m_valueAnim;
};


inline ProgressBar::ProgressBar()
    : m_value(0)
    , m_targetValue(0)
    , m_backgroundColor(0.2f, 0.2f, 0.3f, 0.5f)
    , m_progressColorStart(0.2f, 0.6f, 1.0f, 1.0f)
    , m_progressColorEnd(0.4f, 0.8f, 1.0f, 1.0f)
    , m_borderRadius(10.0f)
    , m_height(8.0f)
    , m_showPercentage(true)
    , m_useGradient(true) {
    
    m_valueAnim = std::make_unique<Animation>();
    m_valueAnim->OnUpdate([this](float value) {
        m_value = value;
    });
}

inline ProgressBar::~ProgressBar() = default;

inline void ProgressBar::SetValue(float value) {
    m_targetValue = std::max(0.0f, std::min(1.0f, value));
    m_value = m_targetValue;
    if (m_valueAnim->IsPlaying()) m_valueAnim->Stop();
}

inline void ProgressBar::SetTargetValue(float value, float duration) {
    m_targetValue = std::max(0.0f, std::min(1.0f, value));
    m_valueAnim->Start(m_value, m_targetValue, duration, EasingType::QuadOut);
}

inline void ProgressBar::SetBackgroundColor(const Color& color) {
    m_backgroundColor = color;
}

inline void ProgressBar::SetProgressColor(const Color& color) {
    m_progressColorStart = color;
    m_progressColorEnd = color;
    m_useGradient = false;
}

inline void ProgressBar::SetProgressGradient(const Color& start, const Color& end) {
    m_progressColorStart = start;
    m_progressColorEnd = end;
    m_useGradient = true;
}

inline void ProgressBar::SetBorderRadius(float radius) {
    m_borderRadius = radius;
}

inline void ProgressBar::SetShowPercentage(bool show) {
    m_showPercentage = show;
}

inline void ProgressBar::SetHeight(float height) {
    m_height = height;
}

inline void ProgressBar::Draw(Renderer* renderer) {
    if (!m_visible || !renderer) return;
    // v4：owner-draw 接管（进度视觉全交回调，值可从外部状态取）
    if (m_paintFn) {
        m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
        return;
    }
    // v6：声明式视觉模板
    if (m_tplId) {
        RenderTemplate(this, renderer, StyleFlags());
        return;
    }

    float radius = m_style.radiusSet ? m_style.radius : m_borderRadius;

    RoundedRect bgRect;
    bgRect.rect = Rect(m_bounds.left, m_bounds.top, m_bounds.right, m_bounds.top + m_height);
    bgRect.radiusX = radius;
    bgRect.radiusY = radius;
    Color trackBg;
    if (!ResolvedStateBg(StyleFlags(), trackBg)) trackBg = m_backgroundColor;
    renderer->FillRoundedRect(bgRect, trackBg);


    float progressWidth = m_bounds.Width() * m_value;
    if (progressWidth > 0) {
        RoundedRect progressRect;
        progressRect.rect = Rect(m_bounds.left, m_bounds.top, m_bounds.left + progressWidth, m_bounds.top + m_height);
        progressRect.radiusX = radius;
        progressRect.radiusY = radius;

        if (m_style.bgGradSet) {
            renderer->FillRoundedRectGradient(progressRect, m_style.bgGrad1, m_style.bgGrad2,
                                              m_style.bgGradVertical != 0);
        } else if (m_useGradient) {
            renderer->FillRoundedRectGradient(progressRect, m_progressColorStart, m_progressColorEnd, true);
        } else {
            renderer->FillRoundedRect(progressRect, m_progressColorStart);
        }
    }

    if (m_style.borderSet) {
        renderer->DrawRoundedRect(bgRect, m_style.borderColor, m_style.borderWidth);
    }
    
    
    if (m_showPercentage) {
        wchar_t text[32];
        swprintf_s(text, L"%.0f%%", m_value * 100);
        
        Rect textRect = m_bounds;
        textRect.top -= 25;
        textRect.bottom = textRect.top + 20;
        renderer->DrawText(text, textRect, Color(0.7f, 0.7f, 0.85f, 0.9f), 12.0f, false, true);
    }
}

inline void ProgressBar::Update(float deltaTime) {
    if (m_valueAnim && m_valueAnim->IsPlaying()) {
        m_valueAnim->Update(deltaTime);
    }
}

} 