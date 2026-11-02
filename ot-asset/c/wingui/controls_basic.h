

#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include <functional>
#include <vector>
#include <set>
#ifndef IS_SURROGATE
#define IS_SURROGATE(ch) (((ch) & 0xFFFFF800) == 0xD800)
#endif
namespace gui
{

    
    
    
    class CheckBox : public UIComponent
    {
    public:
        using StateCallback = std::function<void(bool checked)>;

        CheckBox()
            : m_checked(false), m_checkColor(0.2f, 0.6f, 1.0f, 1.0f), m_boxColor(0.25f, 0.25f, 0.35f, 0.9f), m_textColor(0.9f, 0.9f, 0.95f, 1.0f), m_fontSize(14.0f), m_boxSize(18.0f), m_borderRadius(4.0f), m_isHovered(false)
        {

            m_checkAnim = std::make_unique<Animation>();
            m_checkAnim->OnUpdate([this](float value)
                                  { m_checkProgress = value; });
            m_checkProgress = 0;
        }

        void SetText(const std::wstring &text) { m_text = text; }
        void SetChecked(bool checked)
        {
            m_checked = checked;
            m_checkAnim->Start(m_checkProgress, checked ? 1.0f : 0.0f, 0.2f, EasingType::QuadOut);
            if (m_callback)
                m_callback(m_checked);
        }
        bool IsChecked() const { return m_checked; }
        void SetCheckColor(const Color &color) { m_checkColor = color; }
        void OnChecked(StateCallback callback) { m_callback = callback; }

        void Draw(Renderer *renderer) override
        {
            if (!m_visible || !renderer)
                return;
            // v4：owner-draw 接管（勾选动画进度可经 StyleFlags 之外的原语自绘）
            if (m_paintFn) {
                m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
                return;
            }
            unsigned flags = StyleFlags();

            float boxX = m_bounds.left;
            float boxY = m_bounds.top + (m_bounds.Height() - m_boxSize) / 2;


            RoundedRect boxRect;
            boxRect.rect = Rect(boxX, boxY, boxX + m_boxSize, boxY + m_boxSize);
            boxRect.radiusX = m_style.radiusSet ? m_style.radius : m_borderRadius;
            boxRect.radiusY = boxRect.radiusX;
            Color boxBg;
            if (!ResolvedStateBg(flags, boxBg)) boxBg = m_boxColor;
            renderer->FillRoundedRect(boxRect, boxBg);
            renderer->DrawRoundedRect(boxRect, m_style.borderSet ? m_style.borderColor : Color(0.4f, 0.4f, 0.5f, 0.5f),
                                      m_style.borderSet ? m_style.borderWidth : 1.0f);

            
            if (m_checkProgress > 0.01f)
            {
                float progress = m_checkProgress;
                float padding = m_boxSize * 0.2f * (1 - progress);
                float checkLeft = boxX + padding;
                float checkRight = boxX + m_boxSize - padding;
                float checkTop = boxY + padding;
                float checkBottom = boxY + m_boxSize - padding;

                
                Point p1(checkLeft + (checkRight - checkLeft) * 0.3f, checkTop + (checkBottom - checkTop) * 0.5f);
                Point p2(checkLeft + (checkRight - checkLeft) * 0.45f, checkTop + (checkBottom - checkTop) * 0.7f);
                Point p3(checkLeft + (checkRight - checkLeft) * 0.7f, checkTop + (checkBottom - checkTop) * 0.3f);

                renderer->DrawLine(p1, p2, m_checkColor, 2.0f);
                renderer->DrawLine(p2, p3, m_checkColor, 2.0f);
            }


            Rect textRect(boxX + m_boxSize + 8, m_bounds.top, m_bounds.right, m_bounds.bottom);
            Color tc;
            if (!ResolvedStateText(flags, tc)) tc = m_textColor;
            renderer->DrawText(m_text, textRect, tc, m_fontSize, false, false);
        }

        // v4：状态位（hover 补充基类 disabled）
        unsigned StyleFlags() const override
        {
            unsigned f = UIComponent::StyleFlags();
            if (m_isHovered) f |= 0x1;
            return f;
        }

        void Update(float deltaTime) override
        {
            if (m_checkAnim && m_checkAnim->IsPlaying())
                m_checkAnim->Update(deltaTime);
        }

        bool HandleMouseMove(const Point &mousePos) override
        {
            if (!m_enabled || !m_visible)
                return false;
            bool inside = IsPointInside(mousePos);
            m_isHovered = inside;
            return inside;
        }

        bool HandleMouseDown(const Point &mousePos, int button) override
        {
            if (!m_enabled || !m_visible || button != 0)
                return false;
            if (IsPointInside(mousePos))
            {
                SetChecked(!m_checked);
                return true;
            }
            return false;
        }

    private:
        bool IsPointInside(const Point &p) const
        {
            return p.x >= m_bounds.left && p.x <= m_bounds.right &&
                   p.y >= m_bounds.top && p.y <= m_bounds.bottom;
        }

        std::wstring m_text;
        bool m_checked;
        Color m_checkColor;
        Color m_boxColor;
        Color m_textColor;
        float m_fontSize;
        float m_boxSize;
        float m_borderRadius;
        bool m_isHovered;

        std::unique_ptr<Animation> m_checkAnim;
        float m_checkProgress;
        StateCallback m_callback;
    };

    
    
    
    class RadioButton : public UIComponent
    {
    public:
        using StateCallback = std::function<void(bool selected)>;

        RadioButton()
            : m_selected(false), m_groupId(0), m_selectedColor(0.2f, 0.6f, 1.0f, 1.0f), m_circleColor(0.25f, 0.25f, 0.35f, 0.9f), m_textColor(0.9f, 0.9f, 0.95f, 1.0f), m_fontSize(14.0f), m_circleSize(18.0f), m_isHovered(false)
        {

            m_selectAnim = std::make_unique<Animation>();
            m_selectAnim->OnUpdate([this](float value)
                                   { m_selectProgress = value; });
            m_selectProgress = 0;
        }

        void SetText(const std::wstring &text) { m_text = text; }
        void SetSelected(bool selected)
        {
            m_selected = selected;
            m_selectAnim->Start(m_selectProgress, selected ? 1.0f : 0.0f, 0.2f, EasingType::QuadOut);
            if (m_callback)
                m_callback(m_selected);
        }
        bool IsSelected() const { return m_selected; }
        void SetGroupId(int id) { m_groupId = id; }
        int GetGroupId() const { return m_groupId; }
        void SetSelectedColor(const Color &color) { m_selectedColor = color; }
        void OnSelected(StateCallback callback) { m_callback = callback; }


        void Draw(Renderer *renderer) override
        {
            if (!m_visible || !renderer)
                return;
            if (m_paintFn) {
                m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
                return;
            }

            float cx = m_bounds.left + m_circleSize / 2;
            float cy = m_bounds.top + (m_bounds.Height() - m_circleSize) / 2 + m_circleSize / 2;


            Ellipse outer(cx, cy, m_circleSize / 2);
            Color circleBg;
            if (!ResolvedStateBg(StyleFlags(), circleBg)) circleBg = m_circleColor;
            renderer->FillEllipse(outer, circleBg);
            renderer->DrawEllipse(outer, Color(0.4f, 0.4f, 0.5f, 0.5f), 1.0f);

            
            if (m_selectProgress > 0.01f)
            {
                float innerRadius = (m_circleSize / 2 - 3) * m_selectProgress;
                Ellipse inner(cx, cy, innerRadius);
                renderer->FillEllipse(inner, m_selectedColor);
            }

            
            Rect textRect(m_bounds.left + m_circleSize + 8, m_bounds.top, m_bounds.right, m_bounds.bottom);
            renderer->DrawText(m_text, textRect, m_textColor, m_fontSize, false, false);
        }

        void Update(float deltaTime) override
        {
            if (m_selectAnim && m_selectAnim->IsPlaying())
                m_selectAnim->Update(deltaTime);
        }

        bool HandleMouseDown(const Point &mousePos, int button) override
        {
            if (!m_enabled || !m_visible || button != 0)
                return false;
            if (IsPointInside(mousePos))
            {
                SetSelected(true);
                return true;
            }
            return false;
        }

    private:
        bool IsPointInside(const Point &p) const
        {
            return p.x >= m_bounds.left && p.x <= m_bounds.right &&
                   p.y >= m_bounds.top && p.y <= m_bounds.bottom;
        }

        std::wstring m_text;
        bool m_selected;
        int m_groupId;
        Color m_selectedColor;
        Color m_circleColor;
        Color m_textColor;
        float m_fontSize;
        float m_circleSize;
        bool m_isHovered;

        std::unique_ptr<Animation> m_selectAnim;
        float m_selectProgress;
        StateCallback m_callback;
    };

    
    
    
    class RadioGroup
    {
    public:
        void Add(RadioButton *rb)
        {
            m_buttons.push_back(rb);
        }

        void SetSelected(int index)
        {
            for (size_t i = 0; i < m_buttons.size(); ++i)
            {
                m_buttons[i]->SetSelected(i == index);
            }
        }

        void Clear()
        {
            m_buttons.clear();
        }

    private:
        std::vector<RadioButton *> m_buttons;
    };

    
    
    
    class ToggleSwitch : public UIComponent
    {
    public:
        using StateCallback = std::function<void(bool checked)>;

        ToggleSwitch()
            : m_checked(false), m_offColor(0.35f, 0.35f, 0.45f, 0.9f), m_onColor(0.2f, 0.6f, 1.0f, 1.0f), m_thumbColor(1.0f, 1.0f, 1.0f, 1.0f), m_width(50.0f), m_height(26.0f), m_thumbSize(22.0f), m_borderRadius(13.0f), m_isHovered(false)
        {

            m_switchAnim = std::make_unique<Animation>();
            m_switchAnim->OnUpdate([this](float value)
                                   { m_thumbOffset = value; });
            m_thumbOffset = 0;
        }

        void SetChecked(bool checked)
        {
            m_checked = checked;
            m_switchAnim->Start(m_thumbOffset, checked ? 1.0f : 0.0f, 0.2f, EasingType::QuadOut);
            if (m_callback)
                m_callback(m_checked);
        }
        bool IsChecked() const { return m_checked; }
        void SetOnColor(const Color &color) { m_onColor = color; }
        void SetOffColor(const Color &color) { m_offColor = color; }
        void OnToggle(StateCallback callback) { m_callback = callback; }

        void Draw(Renderer *renderer) override
        {
            if (!m_visible || !renderer)
                return;
            if (m_paintFn) {
                m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
                return;
            }

            float cx = m_bounds.left + m_width / 2;
            float cy = m_bounds.top + (m_bounds.Height() - m_height) / 2 + m_height / 2;


            RoundedRect bgRect;
            bgRect.rect = Rect(m_bounds.left, cy - m_height / 2, m_bounds.left + m_width, cy + m_height / 2);
            bgRect.radiusX = m_style.radiusSet ? m_style.radius : m_borderRadius;
            bgRect.radiusY = bgRect.radiusX;

            Color bgColor;
            if (!ResolvedStateBg(StyleFlags(), bgColor))
                bgColor = Color::Lerp(m_offColor, m_onColor, m_thumbOffset);
            renderer->FillRoundedRect(bgRect, bgColor);

            
            float thumbX = m_bounds.left + (m_thumbOffset * (m_width - m_thumbSize));
            Ellipse thumb(thumbX + m_thumbSize / 2, cy, m_thumbSize / 2);
            renderer->FillEllipse(thumb, m_thumbColor);
        }

        void Update(float deltaTime) override
        {
            if (m_switchAnim && m_switchAnim->IsPlaying())
                m_switchAnim->Update(deltaTime);
        }

        bool HandleMouseDown(const Point &mousePos, int button) override
        {
            if (!m_enabled || !m_visible || button != 0)
                return false;
            if (IsPointInside(mousePos))
            {
                SetChecked(!m_checked);
                return true;
            }
            return false;
        }

    private:
        bool IsPointInside(const Point &p) const
        {
            float cy = m_bounds.top + (m_bounds.Height() - m_height) / 2 + m_height / 2;
            float left = m_bounds.left;
            float right = m_bounds.left + m_width;
            float top = cy - m_height / 2;
            float bottom = cy + m_height / 2;
            return p.x >= left && p.x <= right && p.y >= top && p.y <= bottom;
        }

        bool m_checked;
        Color m_offColor;
        Color m_onColor;
        Color m_thumbColor;
        float m_width;
        float m_height;
        float m_thumbSize;
        float m_borderRadius;
        bool m_isHovered;

        std::unique_ptr<Animation> m_switchAnim;
        float m_thumbOffset;
        StateCallback m_callback;
    };

    
    
    
   


class TextBox : public UIComponent {
public:
    using TextCallback = std::function<void(const std::wstring &text)>;

    TextBox()
        : m_text(L""), m_placeholder(L"Enter text..."), 
          m_backgroundColor(0.18f, 0.18f, 0.25f, 1.0f), 
          m_borderColor(0.35f, 0.35f, 0.45f, 1.0f), 
          m_focusedBorderColor(0.2f, 0.6f, 1.0f, 1.0f), 
          m_textColor(0.95f, 0.95f, 1.0f, 1.0f), 
          m_placeholderColor(0.5f, 0.5f, 0.6f, 1.0f), 
          m_fontSize(14.0f), 
          m_borderRadius(6.0f), 
          m_padding(8.0f), 
          m_hasFocus(false), 
          m_cursorPos(0), 
          m_cursorBlinkTime(0),
          m_scrollOffset(0)  
    {}

    void SetText(const std::wstring &text) {
        m_text = text;
        m_cursorPos = (int)text.length();
        
        m_scrollOffset = 0;
        if (m_callback) m_callback(m_text);
    }
    
    std::wstring GetText() const { return m_text; }
    void SetPlaceholder(const std::wstring &text) { m_placeholder = text; }
    void SetBackgroundColor(const Color &color) { m_backgroundColor = color; }
    void SetBorderColor(const Color &color) { m_borderColor = color; }
    void SetFocusedBorderColor(const Color &color) { m_focusedBorderColor = color; }
    void SetTextColor(const Color &color) { m_textColor = color; }
    void SetFontSize(float size) { m_fontSize = size; }
    void SetBorderRadius(float radius) { m_borderRadius = radius; }
    void SetPadding(float padding) { m_padding = padding; }  
    void OnTextChanged(TextCallback callback) { m_callback = callback; }

    void SetFocus(bool focus) {
        m_hasFocus = focus;
        m_cursorBlinkTime = 0;
        if (focus) {
            
            UpdateScrollToCursor();
        }
    }

    void HandleImeComposition(const std::wstring& compositionString) {
        m_imeComposition = compositionString;
        m_cursorBlinkTime = 0;
        
        if (m_hasFocus) {
            UpdateScrollToCursorWithIme();
        }
    }
    
    void HandleImeCommit(const std::wstring& text) {
        if (!text.empty()) {
            m_text.insert(m_cursorPos, text);
            m_cursorPos += (int)text.length();
            if (m_callback) m_callback(m_text);
            
            UpdateScrollToCursor();
        }
        m_imeComposition.clear();
    }
    
    void SetImeComposing(bool composing) {
        m_isImeComposing = composing;
        if (!composing) {
            m_imeComposition.clear();
        }
        m_cursorBlinkTime = 0;
        if (m_hasFocus) {
            UpdateScrollToCursor();
        }
    }

    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;
        // v4：owner-draw 接管（文字/caret/IME 高级定制全交回调；
        // state 0x8 focused 可用，文本与光标自绘）
        if (m_paintFn) {
            m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
            return;
        }


        RoundedRect bgRect;
        bgRect.rect = m_bounds;
        bgRect.radiusX = m_style.radiusSet ? m_style.radius : m_borderRadius;
        bgRect.radiusY = bgRect.radiusX;
        Color bg;
        if (!ResolvedStateBg(StyleFlags(), bg)) bg = m_backgroundColor;
        renderer->FillRoundedRect(bgRect, bg);


        Color borderColor;
        if (m_style.borderSet) borderColor = m_style.borderColor;
        else borderColor = m_hasFocus ? m_focusedBorderColor : m_borderColor;
        renderer->DrawRoundedRect(bgRect, borderColor, m_style.borderSet ? m_style.borderWidth : 1.5f);
        
        
        Rect textRect = m_bounds;
        textRect.left += m_padding;
        textRect.right -= m_padding;
        
        
        renderer->PushClip(textRect);
        
        if (m_hasFocus && m_isImeComposing && !m_imeComposition.empty()) {
            
            DrawWithImeComposition(renderer, textRect);
        } else if (m_hasFocus) {
            
            DrawWithCursor(renderer, textRect);
        } else {
            
            DrawNormal(renderer, textRect);
        }
        
        renderer->PopClip();
    }

    void Update(float deltaTime) override {
        m_cursorBlinkTime += deltaTime;
    }

    bool HandleMouseDown(const Point &mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0)
            return false;

        bool inside = IsPointInside(mousePos);
        SetFocus(inside);
        
        
        if (inside && m_renderer) {
            UpdateCursorFromMouse(mousePos);
        }
        
        return inside;
    }

    void HandleChar(wchar_t ch) {
        if (!m_hasFocus) return;
        
        if (m_isImeComposing) return;
        
        if (ch == L'\b') {
            if (m_cursorPos > 0) {
                m_text.erase(m_cursorPos - 1, 1);
                m_cursorPos--;
                if (m_callback) m_callback(m_text);
                UpdateScrollToCursor();
            }
        } else if (ch == L'\r') {
            if (m_callback) m_callback(m_text);
        } else if (ch >= 32 && ch < 127) {
            m_text.insert(m_cursorPos, 1, ch);
            m_cursorPos++;
            if (m_callback) m_callback(m_text);
            UpdateScrollToCursor();
        }
        
        m_cursorBlinkTime = 0;
    }

    void HandleKey(int vkey) {
        if (!m_hasFocus) return;

        if (vkey == VK_LEFT && m_cursorPos > 0) {
            m_cursorPos--;
            UpdateScrollToCursor();
            m_cursorBlinkTime = 0;
        }
        else if (vkey == VK_RIGHT && m_cursorPos < (int)m_text.length()) {
            m_cursorPos++;
            UpdateScrollToCursor();
            m_cursorBlinkTime = 0;
        }
        else if (vkey == VK_DELETE && m_cursorPos < (int)m_text.length()) {
            m_text.erase(m_cursorPos, 1);
            if (m_callback) m_callback(m_text);
            UpdateScrollToCursor();
            m_cursorBlinkTime = 0;
        }
        else if (vkey == VK_HOME) {
            m_cursorPos = 0;
            UpdateScrollToCursor();
            m_cursorBlinkTime = 0;
        }
        else if (vkey == VK_END) {
            m_cursorPos = (int)m_text.length();
            UpdateScrollToCursor();
            m_cursorBlinkTime = 0;
        }
    }

    bool HasFocus() const { return m_hasFocus; }
    
    
    void SetRenderer(Renderer* renderer) { m_renderer = renderer; }

private:
    bool IsPointInside(const Point &p) const {
        return p.x >= m_bounds.left && p.x <= m_bounds.right &&
               p.y >= m_bounds.top && p.y <= m_bounds.bottom;
    }

    // v4：生效文字色（style/state 叠加 > 组件默认）
    Color TextColorEff() const {
        Color c;
        if (ResolvedStateText(StyleFlags(), c)) return c;
        return m_textColor;
    }

public:
    // v4：状态位（focused 补充基类 disabled）
    unsigned StyleFlags() const override {
        unsigned f = UIComponent::StyleFlags();
        if (m_hasFocus) f |= 0x8;
        return f;
    }

private:
    
    
    float GetTextWidthBeforeCursor() {
        if (!m_renderer) return 0;
        std::wstring textBefore = m_text.substr(0, m_cursorPos);
        Size size = m_renderer->MeasureText(textBefore, m_fontSize, false);
        return size.width;
    }
    
    
    float GetFullTextWidth() {
        if (!m_renderer) return 0;
        Size size = m_renderer->MeasureText(m_text, m_fontSize, false);
        return size.width;
    }
    
    
    float GetTextAreaWidth() {
        return m_bounds.Width() - m_padding * 2;
    }
    
    
    void UpdateScrollToCursor() {
        if (!m_renderer) return;
        
        float cursorX = GetTextWidthBeforeCursor();
        float textAreaWidth = GetTextAreaWidth();
        
        
        if (cursorX - m_scrollOffset > textAreaWidth - 10) {
            m_scrollOffset = cursorX - textAreaWidth + 10;
        }
        
        else if (cursorX - m_scrollOffset < 10) {
            m_scrollOffset = std::max(0.0f, cursorX - 10);
        }
        
        
        float maxScroll = std::max(0.0f, GetFullTextWidth() - textAreaWidth);
        m_scrollOffset = std::max(0.0f, std::min(maxScroll, m_scrollOffset));
    }
    
    
    void UpdateScrollToCursorWithIme() {
        if (!m_renderer) return;
        
        
        std::wstring textWithIme = m_text + m_imeComposition;
        Size size = m_renderer->MeasureText(textWithIme, m_fontSize, false);
        float totalWidth = size.width;
        float textAreaWidth = GetTextAreaWidth();
        
        if (totalWidth - m_scrollOffset > textAreaWidth - 10) {
            m_scrollOffset = totalWidth - textAreaWidth + 10;
        }
        
        float maxScroll = std::max(0.0f, totalWidth - textAreaWidth);
        m_scrollOffset = std::max(0.0f, std::min(maxScroll, m_scrollOffset));
    }
    
    
    void UpdateCursorFromMouse(const Point& mousePos) {
        if (!m_renderer) return;
        
        float clickX = mousePos.x - m_bounds.left - m_padding + m_scrollOffset;
        
        
        int newPos = 0;
        for (size_t i = 0; i <= m_text.length(); i++) {
            std::wstring substr = m_text.substr(0, i);
            Size size = m_renderer->MeasureText(substr, m_fontSize, false);
            if (size.width >= clickX) {
                newPos = (int)i;
                break;
            }
            newPos = (int)i;
        }
        
        m_cursorPos = newPos;
        UpdateScrollToCursor();
    }
    
    
    void DrawNormal(Renderer* renderer, const Rect& textRect) {
        if (m_text.empty()) {
            renderer->DrawText(m_placeholder, textRect, m_placeholderColor, m_fontSize, false, false);
        } else {

            Rect scrolledRect = textRect;
            scrolledRect.left -= m_scrollOffset;
            scrolledRect.right -= m_scrollOffset;
            renderer->DrawText(m_text, scrolledRect, TextColorEff(), m_fontSize, false, false);
        }
    }
    
    
    void DrawWithCursor(Renderer* renderer, const Rect& textRect) {
        bool showCursor = (static_cast<int>(m_cursorBlinkTime * 2.0f) % 2) == 0;
        
        
        Rect scrolledRect = textRect;
        scrolledRect.left -= m_scrollOffset;
        scrolledRect.right -= m_scrollOffset;
        

        renderer->DrawText(m_text, scrolledRect, TextColorEff(), m_fontSize, false, false);


        if (showCursor) {
            float cursorX = GetTextWidthBeforeCursor() - m_scrollOffset + textRect.left;
            if (cursorX >= textRect.left - 2 && cursorX <= textRect.right + 2) {
                renderer->DrawLine(
                    Point(cursorX, textRect.top + 2),
                    Point(cursorX, textRect.bottom - 2),
                    TextColorEff(), 1.5f);
            }
        }
    }
    
    
    void DrawWithImeComposition(Renderer* renderer, const Rect& textRect) {
        bool showCursor = (static_cast<int>(m_cursorBlinkTime * 2.0f) % 2) == 0;
        
        
        if (!m_text.empty()) {
            Rect scrolledRect = textRect;
            scrolledRect.left -= m_scrollOffset;
            scrolledRect.right -= m_scrollOffset;
            renderer->DrawText(m_text, scrolledRect, TextColorEff(), m_fontSize, false, false);
        }
        
        
        Size textSize = renderer->MeasureText(m_text, m_fontSize, false);
        float textWidth = textSize.width;
        
        
        float compX = textRect.left + textWidth - m_scrollOffset;
        Rect compRect = textRect;
        compRect.left = compX;
        
        
        renderer->DrawText(m_imeComposition, compRect, 
            Color(m_textColor.r, m_textColor.g, m_textColor.b, 0.85f), 
            m_fontSize, false, false);
        
        
        Size compSize = renderer->MeasureText(m_imeComposition, m_fontSize, false);
        float lineY = textRect.top + compSize.height;
        renderer->DrawLine(
            Point(compX, lineY),
            Point(compX + compSize.width, lineY),
            Color(m_textColor.r, m_textColor.g, m_textColor.b, 0.6f), 1.0f);
        
        
        if (showCursor) {
            float cursorX = compX + compSize.width;
            if (cursorX >= textRect.left - 2 && cursorX <= textRect.right + 2) {
                renderer->DrawLine(
                    Point(cursorX, textRect.top + 2),
                    Point(cursorX, textRect.bottom - 2),
                    m_textColor, 1.5f);
            }
        }
    }

    std::wstring m_text;
    std::wstring m_placeholder;
    Color m_backgroundColor;
    Color m_borderColor;
    Color m_focusedBorderColor;
    Color m_textColor;
    Color m_placeholderColor;
    float m_fontSize;
    float m_borderRadius;
    float m_padding;
    bool m_hasFocus;
    int m_cursorPos;
    float m_cursorBlinkTime;
    std::wstring m_imeComposition;
    bool m_isImeComposing = false;
    TextCallback m_callback;
    
    
    float m_scrollOffset;      
    Renderer* m_renderer = nullptr;  
};

} 