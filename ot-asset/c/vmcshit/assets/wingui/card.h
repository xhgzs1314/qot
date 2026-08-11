
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include <vector>

namespace gui {


class Card : public UIComponent {
public:
    Card();
    ~Card();
    
    void SetTitle(const std::wstring& title);
    void SetSubtitle(const std::wstring& subtitle);
    void SetBackgroundColor(const Color& color);
    void SetBorderColor(const Color& color);
    void SetBorderRadius(float radius);
    void SetElevation(float elevation);  
    void AddChild(std::unique_ptr<UIComponent> child);
    
    void Draw(Renderer* renderer) override;
    void Update(float deltaTime) override;
    bool HandleMouseMove(const Point& mousePos) override;
    bool HandleMouseDown(const Point& mousePos, int button) override;
    bool HandleMouseUp(const Point& mousePos, int button) override;
    
    void SetOnClick(std::function<void()> callback) { m_clickCallback = callback; }
    
private:
    void DrawShadow(Renderer* renderer);
    
    std::wstring m_title;
    std::wstring m_subtitle;
    Color m_backgroundColor;
    Color m_borderColor;
    float m_borderRadius;
    float m_elevation;
    std::vector<std::unique_ptr<UIComponent>> m_children;
    
    bool m_isHovered;
    std::unique_ptr<Animation> m_hoverAnim;
    float m_currentHoverScale;
    
    std::function<void()> m_clickCallback;
};


inline Card::Card()
    : m_title(L"Card Title")
    , m_subtitle(L"Card subtitle text")
    , m_backgroundColor(0.12f, 0.12f, 0.18f, 0.95f)
    , m_borderColor(0.3f, 0.3f, 0.4f, 0.5f)
    , m_borderRadius(12.0f)
    , m_elevation(4.0f)
    , m_isHovered(false)
    , m_currentHoverScale(1.0f) {
    
    m_hoverAnim = std::make_unique<Animation>();
    m_hoverAnim->OnUpdate([this](float value) {
        m_currentHoverScale = 1.0f + value * 0.02f;
    });
}

inline Card::~Card() = default;

inline void Card::SetTitle(const std::wstring& title) {
    m_title = title;
}

inline void Card::SetSubtitle(const std::wstring& subtitle) {
    m_subtitle = subtitle;
}

inline void Card::SetBackgroundColor(const Color& color) {
    m_backgroundColor = color;
}

inline void Card::SetBorderColor(const Color& color) {
    m_borderColor = color;
}

inline void Card::SetBorderRadius(float radius) {
    m_borderRadius = radius;
}

inline void Card::SetElevation(float elevation) {
    m_elevation = elevation;
}

inline void Card::AddChild(std::unique_ptr<UIComponent> child) {
    m_children.push_back(std::move(child));
}

inline void Card::DrawShadow(Renderer* renderer) {
    
    float offset = m_elevation * 0.5f;
    for (int i = 3; i >= 0; i--) {
        float alpha = (0.08f - i * 0.015f) * std::min(1.0f, m_elevation / 10.0f);
        RoundedRect shadowRect;
        shadowRect.rect = Rect(
            m_bounds.left + offset + i,
            m_bounds.top + offset + i,
            m_bounds.right + offset + i,
            m_bounds.bottom + offset + i
        );
        shadowRect.radiusX = m_borderRadius;
        shadowRect.radiusY = m_borderRadius;
        renderer->FillRoundedRect(shadowRect, Color(0, 0, 0, alpha));
    }
}

inline void Card::Draw(Renderer* renderer) {
    if (!m_visible || !renderer) return;
    
    DrawShadow(renderer);
    
    
    float width = m_bounds.Width();
    float height = m_bounds.Height();
    float centerX = (m_bounds.left + m_bounds.right) / 2;
    float centerY = (m_bounds.top + m_bounds.bottom) / 2;
    float scaledWidth = width * m_currentHoverScale;
    float scaledHeight = height * m_currentHoverScale;
    
    Rect adjustedBounds(
        centerX - scaledWidth / 2,
        centerY - scaledHeight / 2,
        centerX + scaledWidth / 2,
        centerY + scaledHeight / 2
    );
    
    RoundedRect cardRect;
    cardRect.rect = adjustedBounds;
    cardRect.radiusX = m_borderRadius;
    cardRect.radiusY = m_borderRadius;
    
    renderer->FillRoundedRect(cardRect, m_backgroundColor);
    renderer->DrawRoundedRect(cardRect, m_borderColor, 1.5f);
    
    
    Rect titleRect = adjustedBounds;
    titleRect.top += 20;
    titleRect.bottom = titleRect.top + 30;
    renderer->DrawText(m_title, titleRect, Color(1, 1, 1, 0.95f), 18.0f, true, true);
    
    
    Rect subtitleRect = adjustedBounds;
    subtitleRect.top += 55;
    subtitleRect.bottom = subtitleRect.top + 25;
    renderer->DrawText(m_subtitle, subtitleRect, Color(0.7f, 0.7f, 0.85f, 0.8f), 12.0f, false, true);
    
    
    for (auto& child : m_children) {
        child->Draw(renderer);
    }
}

inline void Card::Update(float deltaTime) {
    if (m_hoverAnim && m_hoverAnim->IsPlaying()) {
        m_hoverAnim->Update(deltaTime);
    }
    
    for (auto& child : m_children) {
        child->Update(deltaTime);
    }
}

inline bool Card::HandleMouseMove(const Point& mousePos) {
    if (!m_enabled || !m_visible) return false;
    
    bool inside = mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
                  mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom;
    
    if (inside && !m_isHovered) {
        m_isHovered = true;
        m_hoverAnim->Start(0, 1, 0.25f, EasingType::QuadOut);
    } else if (!inside && m_isHovered) {
        m_isHovered = false;
        m_hoverAnim->Start(1, 0, 0.25f, EasingType::QuadOut);
    }
    for (auto& child : m_children) {
        child->HandleMouseMove(mousePos);
    }
    
    return inside;
}

inline bool Card::HandleMouseDown(const Point& mousePos, int button) {
    bool inside = mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
                  mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom;
    
    if (inside && button == 0 && m_clickCallback) {
        m_clickCallback();
        return true;
    }
    
    for (auto& child : m_children) {
        if (child->HandleMouseDown(mousePos, button)) return true;
    }
    
    return false;
}

inline bool Card::HandleMouseUp(const Point& mousePos, int button) {
    for (auto& child : m_children) {
        if (child->HandleMouseUp(mousePos, button)) return true;
    }
    return false;
}

} 