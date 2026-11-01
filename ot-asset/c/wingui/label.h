
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include <string>

namespace gui {

// Label：静态文本。wingui 原先缺这个控件（文档里只能拿禁用按钮顶替），
// 没有它就无法做正常的界面（标题、说明、状态提示全要它）。
class Label : public UIComponent {
public:
    enum class HAlign { Left, Center, Right };
    enum class VAlign { Top, Middle, Bottom };

    Label() : m_text(L"Label")
            , m_textColor(0.92f, 0.93f, 0.97f, 1.0f)
            , m_fontSize(15.0f)
            , m_bold(false)
            , m_halign(HAlign::Left)
            , m_valign(VAlign::Middle)
            , m_wordWrap(false) {}

    void SetText(const std::wstring& text) { m_text = text; }
    std::wstring GetText() const { return m_text; }
    void SetTextColor(const Color& color) { m_textColor = color; }
    void SetFontSize(float size) { m_fontSize = size; }
    void SetBold(bool bold) { m_bold = bold; }
    void SetAlign(HAlign h, VAlign v) { m_halign = h; m_valign = v; }
    void SetWordWrap(bool wrap) { m_wordWrap = wrap; }
    bool IsWordWrap() const { return m_wordWrap; }

    void Draw(Renderer* renderer) override {
        if (!m_text.empty()) {
            renderer->DrawTextAligned(m_text, m_bounds, m_textColor, m_fontSize,
                                      m_bold, m_halign == HAlign::Left ? 0
                                            : (m_halign == HAlign::Center ? 1 : 2),
                                      m_valign == VAlign::Top ? 0
                                            : (m_valign == VAlign::Middle ? 1 : 2),
                                      m_wordWrap);
        }
    }

private:
    std::wstring m_text;
    Color m_textColor;
    float m_fontSize;
    bool m_bold;
    HAlign m_halign;
    VAlign m_valign;
    bool m_wordWrap;
};

}
