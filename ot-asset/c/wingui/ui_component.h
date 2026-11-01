
#pragma once
#include "core_types.h"
#include <functional>
#include <string>

namespace gui {

class UIComponent {
public:
    virtual ~UIComponent() = default;
    
    virtual void Draw(Renderer* renderer) = 0;
    virtual void Update(float deltaTime) {}
    virtual bool HandleMouseMove(const Point& mousePos) { return false; }
    virtual bool HandleMouseDown(const Point& mousePos, int button) { return false; }
    virtual bool HandleMouseUp(const Point& mousePos, int button) { return false; }
    
    void SetBounds(const Rect& bounds) { m_bounds = bounds; }
    Rect GetBounds() const { return m_bounds; }
    void SetVisible(bool visible) { m_visible = visible; }
    bool IsVisible() const { return m_visible; }
    void SetEnabled(bool enabled) { m_enabled = enabled; }
    bool IsEnabled() const { return m_enabled; }
    void SetId(int id) { m_id = id; }
    int GetId() const { return m_id; }
    
protected:
    Rect m_bounds;
    bool m_visible = true;
    bool m_enabled = true;
    int m_id = 0;
};

} 