
#pragma once
#include "core_types.h"
#include <d2d1.h>
#include <dwrite.h>
#include <memory>

namespace gui
{

    
    class Renderer
    {
    public:
        Renderer();
        ~Renderer();

        bool Initialize(HWND hwnd);
        void Resize(const Size &size);
        void BeginDraw();
        void EndDraw();
        void Clear(const Color &color);

        
        void FillRect(const Rect &rect, const Color &color);
        void DrawRect(const Rect &rect, const Color &color, float strokeWidth = 1.0f);
        void FillRoundedRect(const RoundedRect &rect, const Color &color);
        void DrawRoundedRect(const RoundedRect &rect, const Color &color, float strokeWidth = 1.0f);
        void FillEllipse(const Ellipse &ellipse, const Color &color);
        void DrawEllipse(const Ellipse &ellipse, const Color &color, float strokeWidth = 1.0f);
        void DrawLine(const Point &p1, const Point &p2, const Color &color, float strokeWidth = 1.0f);

        
        void FillRectGradient(const Rect &rect, const Color &startColor, const Color &endColor, bool vertical = true);
        void FillRoundedRectGradient(const RoundedRect &rect, const Color &startColor, const Color &endColor, bool vertical = true);
        void PushClip(const Rect &rect)
        {
            if (m_renderTarget)
            {
                m_renderTarget->PushAxisAlignedClip(rect.ToD2D(), D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
            }
        }

        void PopClip()
        {
            if (m_renderTarget)
            {
                m_renderTarget->PopAxisAlignedClip();
            }
        }
        
        void DrawText(const std::wstring &text, const Rect &rect, const Color &color, float fontSize = 16.0f, bool bold = false, bool center = true);
        Size MeasureText(const std::wstring &text, float fontSize = 16.0f, bool bold = false);

        
        ID2D1HwndRenderTarget *GetRenderTarget() { return m_renderTarget; }
        IDWriteFactory *GetWriteFactory() { return m_writeFactory; }

    private:
        void CreateResources();
        void ReleaseResources();
        ID2D1SolidColorBrush *GetBrush(const Color &color);
        IDWriteTextFormat *GetTextFormat(float fontSize, bool bold);

        HWND m_hwnd;
        Size m_size;
        ID2D1Factory *m_d2dFactory;
        ID2D1HwndRenderTarget *m_renderTarget;
        IDWriteFactory *m_writeFactory;

        struct BrushCache
        {
            Color color;
            ID2D1SolidColorBrush *brush;
        };
        std::vector<BrushCache> m_brushCache;

        struct TextFormatCache
        {
            float fontSize;
            bool bold;
            IDWriteTextFormat *format;
        };
        std::vector<TextFormatCache> m_textFormatCache;
    };

} 