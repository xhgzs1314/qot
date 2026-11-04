
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include "visual_template.h"
#include <functional>

namespace gui {


class Slider : public UIComponent {
public:
    using ValueCallback = std::function<void(float)>;
    
    Slider();
    ~Slider();
    
    void SetValue(float value);
    float GetValue() const { return m_value; }
    void SetMinMax(float min, float max);
    void SetOrientation(bool vertical);
    void SetThumbSize(float size);
    void SetTrackColor(const Color& color);
    void SetFillColor(const Color& color);
    void SetThumbColor(const Color& color);
    void OnValueChanged(ValueCallback callback);
    
    void Draw(Renderer* renderer) override;
    void Update(float deltaTime) override;
    bool HandleMouseMove(const Point& mousePos) override;
    bool HandleMouseDown(const Point& mousePos, int button) override;
    bool HandleMouseUp(const Point& mousePos, int button) override;

    // v4：状态位（hover/dragging=pressed 补充基类 disabled）
    unsigned StyleFlags() const override {
        unsigned f = UIComponent::StyleFlags();
        if (m_isHovered) f |= 0x1;
        if (m_isDragging) f |= 0x2;
        return f;
    }

    // ---- v6：模板通道（主值 = 归一化 0..1）----
    float TplValue(int src) const override {
        (void)src;
        float span = m_maxValue - m_minValue;
        if (span <= 0.0f) return 0.0f;
        float v = (m_value - m_minValue) / span;
        if (v < 0.0f) v = 0.0f;
        if (v > 1.0f) v = 1.0f;
        return v;
    }
    
private:
    void UpdateValueFromPosition(const Point& pos);
    float GetPositionFromValue() const;
    
    float m_value;
    float m_minValue;
    float m_maxValue;
    bool m_vertical;
    float m_thumbSize;
    Color m_trackColor;
    Color m_fillColor;
    Color m_thumbColor;
    Color m_thumbHoverColor;
    
    bool m_isDragging;
    bool m_isHovered;
    std::unique_ptr<Animation> m_thumbHoverAnim;
    float m_currentThumbSize;
    
    ValueCallback m_valueCallback;
};


inline Slider::Slider()
    : m_value(0.5f)
    , m_minValue(0)
    , m_maxValue(1)
    , m_vertical(false)
    , m_thumbSize(16.0f)
    , m_trackColor(0.2f, 0.2f, 0.3f, 0.7f)
    , m_fillColor(0.2f, 0.6f, 1.0f, 1.0f)
    , m_thumbColor(0.3f, 0.7f, 1.0f, 1.0f)
    , m_thumbHoverColor(0.4f, 0.8f, 1.0f, 1.0f)
    , m_isDragging(false)
    , m_isHovered(false)
    , m_currentThumbSize(m_thumbSize) {
    
    m_thumbHoverAnim = std::make_unique<Animation>();
    m_thumbHoverAnim->OnUpdate([this](float value) {
        m_currentThumbSize = m_thumbSize + value * 2.0f;
    });
}

inline Slider::~Slider() = default;

inline void Slider::SetValue(float value) {
    m_value = std::max(m_minValue, std::min(m_maxValue, value));
    if (m_valueCallback) m_valueCallback(m_value);
}

inline void Slider::SetMinMax(float min, float max) {
    m_minValue = min;
    m_maxValue = max;
    m_value = std::max(min, std::min(max, m_value));
}

inline void Slider::SetOrientation(bool vertical) {
    m_vertical = vertical;
}

inline void Slider::SetThumbSize(float size) {
    m_thumbSize = size;
    m_currentThumbSize = size;
}

inline void Slider::SetTrackColor(const Color& color) {
    m_trackColor = color;
}

inline void Slider::SetFillColor(const Color& color) {
    m_fillColor = color;
}

inline void Slider::SetThumbColor(const Color& color) {
    m_thumbColor = color;
}

inline void Slider::OnValueChanged(ValueCallback callback) {
    m_valueCallback = callback;
}

inline float Slider::GetPositionFromValue() const {
    float t = (m_value - m_minValue) / (m_maxValue - m_minValue);
    if (m_vertical) {
        return m_bounds.bottom - t * (m_bounds.bottom - m_bounds.top);
    } else {
        return m_bounds.left + t * (m_bounds.right - m_bounds.left);
    }
}

inline void Slider::UpdateValueFromPosition(const Point& pos) {
    float t;
    if (m_vertical) {
        t = (m_bounds.bottom - pos.y) / (m_bounds.bottom - m_bounds.top);
    } else {
        t = (pos.x - m_bounds.left) / (m_bounds.right - m_bounds.left);
    }
    t = std::max(0.0f, std::min(1.0f, t));
    SetValue(m_minValue + t * (m_maxValue - m_minValue));
}

inline void Slider::Draw(Renderer* renderer) {
    if (!m_visible || !renderer) return;
    // v4：owner-draw 接管（值可经回调自行绘制；拖拽语义不变）
    if (m_paintFn) {
        m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
        return;
    }
    // v6：声明式视觉模板
    if (m_tplId) {
        RenderTemplate(this, renderer, StyleFlags());
        return;
    }
    unsigned flags = StyleFlags();

    float trackPos = GetPositionFromValue();
    float halfThumb = m_currentThumbSize / 2;

    // v4 样式叠加：bg = 填充条颜色；border = 边框
    Color fillColor = m_fillColor;
    Color thumbFill = (flags & 0x3) ? m_thumbHoverColor : m_thumbColor;
    Color resolved;
    if (ResolvedStateBg(flags, resolved)) { fillColor = resolved; thumbFill = resolved; }
    float trackThick = 4.0f;

    if (m_vertical) {

        float trackWidth = trackThick;
        float trackCenterX = (m_bounds.left + m_bounds.right) / 2;


        Rect trackRect(trackCenterX - trackWidth/2, m_bounds.top,
                       trackCenterX + trackWidth/2, m_bounds.bottom);
        renderer->FillRoundedRect(RoundedRect{trackRect, trackWidth/2, trackWidth/2}, m_trackColor);


        Rect fillRect(trackCenterX - trackWidth/2, trackPos,
                      trackCenterX + trackWidth/2, m_bounds.bottom);
        renderer->FillRoundedRect(RoundedRect{fillRect, trackWidth/2, trackWidth/2}, fillColor);
        

        Ellipse thumb;
        thumb.center = Point(trackCenterX, trackPos);
        thumb.radiusX = halfThumb;
        thumb.radiusY = halfThumb;
        renderer->FillEllipse(thumb, thumbFill);
    } else {

        float trackHeight = trackThick;
        float trackCenterY = (m_bounds.top + m_bounds.bottom) / 2;


        Rect trackRect(m_bounds.left, trackCenterY - trackHeight/2,
                       m_bounds.right, trackCenterY + trackHeight/2);
        renderer->FillRoundedRect(RoundedRect{trackRect, trackHeight/2, trackHeight/2}, m_trackColor);


        Rect fillRect(m_bounds.left, trackCenterY - trackHeight/2,
                      trackPos, trackCenterY + trackHeight/2);
        renderer->FillRoundedRect(RoundedRect{fillRect, trackHeight/2, trackHeight/2}, fillColor);


        Ellipse thumb;
        thumb.center = Point(trackPos, trackCenterY);
        thumb.radiusX = halfThumb;
        thumb.radiusY = halfThumb;
        renderer->FillEllipse(thumb, thumbFill);
    }

    if (m_style.borderSet) {
        renderer->DrawRect(m_bounds, m_style.borderColor, m_style.borderWidth);
    }
}

inline void Slider::Update(float deltaTime) {
    if (m_thumbHoverAnim && m_thumbHoverAnim->IsPlaying()) {
        m_thumbHoverAnim->Update(deltaTime);
    }
}

inline bool Slider::HandleMouseMove(const Point& mousePos) {
    if (!m_enabled || !m_visible) return false;
    
    
    float trackPos = GetPositionFromValue();
    float halfThumb = m_currentThumbSize / 2;
    
    float thumbLeft = m_vertical ? (m_bounds.left + m_bounds.right)/2 - halfThumb : trackPos - halfThumb;
    float thumbRight = m_vertical ? (m_bounds.left + m_bounds.right)/2 + halfThumb : trackPos + halfThumb;
    float thumbTop = m_vertical ? trackPos - halfThumb : (m_bounds.top + m_bounds.bottom)/2 - halfThumb;
    float thumbBottom = m_vertical ? trackPos + halfThumb : (m_bounds.top + m_bounds.bottom)/2 + halfThumb;
    
    bool overThumb = mousePos.x >= thumbLeft && mousePos.x <= thumbRight &&
                     mousePos.y >= thumbTop && mousePos.y <= thumbBottom;
    
    if (overThumb && !m_isHovered && !m_isDragging) {
        m_isHovered = true;
        m_thumbHoverAnim->Start(0, 1, 0.15f, EasingType::QuadOut);
    } else if (!overThumb && m_isHovered && !m_isDragging) {
        m_isHovered = false;
        m_thumbHoverAnim->Start(1, 0, 0.15f, EasingType::QuadOut);
    }
    
    if (m_isDragging) {
        UpdateValueFromPosition(mousePos);
        return true;
    }
    
    return overThumb;
}

inline bool Slider::HandleMouseDown(const Point& mousePos, int button) {
    if (!m_enabled || !m_visible || button != 0) return false;
    
    m_isDragging = true;
    UpdateValueFromPosition(mousePos);
    return true;
}

inline bool Slider::HandleMouseUp(const Point& mousePos, int button) {
    if (m_isDragging) {
        m_isDragging = false;
        return true;
    }
    return false;
}

} 