
#pragma once

#include "core_types.h"
#include "renderer.h"
#include "animation.h"
#include "ui_component.h"
#include "button.h"
#include "card.h"
#include "label.h"
#include "progress_bar.h"
#include "slider.h"
#include "modern_window.h"
#include "controls_basic.h"
#include "controls_extended.h"
#include "layout_theme.h"
#include "scrollpane.h"
#include "virtual_list.h"
#include <CommCtrl.h>

namespace gui
{

    
    inline bool Initialize()
    {

        INITCOMMONCONTROLSEX icex = {};
        icex.dwSize = sizeof(INITCOMMONCONTROLSEX);
        icex.dwICC = ICC_WIN95_CLASSES;
        InitCommonControlsEx(&icex);
        // COM 初始化（WIC 图片解码 / DComp 相关组件需要；S_FALSE = 已初始化，正常）
        CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        return true;
    }

    inline void Shutdown()
    {
        
    }

    
    inline Color ColorFromHex(int hexColor, float alpha = 1.0f)
    {
        return Color(
            ((hexColor >> 16) & 0xFF) / 255.0f,
            ((hexColor >> 8) & 0xFF) / 255.0f,
            (hexColor & 0xFF) / 255.0f,
            alpha);
    }

    
    namespace Theme
    {
        inline void ApplyDark(ModernWindow &window)
        {
            window.SetBackgroundGradient(
                Color(0.05f, 0.05f, 0.12f, 1.0f),
                Color(0.10f, 0.08f, 0.20f, 1.0f));
        }

        inline void ApplyLight(ModernWindow &window)
        {
            window.SetBackgroundGradient(
                Color(0.95f, 0.95f, 0.98f, 1.0f),
                Color(0.85f, 0.87f, 0.92f, 1.0f));
        }

        inline void ApplyBlue(ModernWindow &window)
        {
            window.SetBackgroundGradient(
                Color(0.1f, 0.2f, 0.4f, 1.0f),
                Color(0.05f, 0.1f, 0.3f, 1.0f));
        }
    }
} 