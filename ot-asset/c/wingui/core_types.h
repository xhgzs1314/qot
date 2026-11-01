
#pragma once
#include <windows.h>
#include <d2d1.h>
#include <d2d1_1.h>
#include <dwrite.h>
#include <memory>
#include <string>
#include <vector>
#include <algorithm>


#ifdef min
#undef min
#endif
#ifdef max
#undef max
#endif

namespace gui {


struct Color {
    float r, g, b, a;
    
    Color() : r(0), g(0), b(0), a(1) {}
    Color(float r, float g, float b, float a = 1.0f) : r(r), g(g), b(b), a(a) {}
    
    static Color FromRGB(int r, int g, int b, int a = 255) {
        return Color(r/255.0f, g/255.0f, b/255.0f, a/255.0f);
    }
    
    static Color Black() { return Color(0, 0, 0, 1); }
    static Color White() { return Color(1, 1, 1, 1); }
    static Color Transparent() { return Color(0, 0, 0, 0); }
    
    
    static Color Lerp(const Color& a, const Color& b, float t) {
        if (t < 0.0f) t = 0.0f;
        if (t > 1.0f) t = 1.0f;
        return Color(
            a.r + (b.r - a.r) * t,
            a.g + (b.g - a.g) * t,
            a.b + (b.b - a.b) * t,
            a.a + (b.a - a.a) * t
        );
    }
    
    D2D1_COLOR_F ToD2D() const { return D2D1::ColorF(r, g, b, a); }
};


struct Point {
    float x, y;
    Point() : x(0), y(0) {}
    Point(float x, float y) : x(x), y(y) {}
};


struct Size {
    float width, height;
    Size() : width(0), height(0) {}
    Size(float w, float h) : width(w), height(h) {}
};


struct Rect {
    float left, top, right, bottom;
    Rect() : left(0), top(0), right(0), bottom(0) {}
    Rect(float l, float t, float r, float b) : left(l), top(t), right(r), bottom(b) {}
    
    float Width() const { return right - left; }
    float Height() const { return bottom - top; }
    Point Center() const { return Point((left+right)/2, (top+bottom)/2); }
    
    D2D1_RECT_F ToD2D() const { return D2D1::RectF(left, top, right, bottom); }
};


struct RoundedRect {
    Rect rect;
    float radiusX, radiusY;
    
    RoundedRect() : radiusX(0), radiusY(0) {}
    RoundedRect(const Rect& r, float rx, float ry) : rect(r), radiusX(rx), radiusY(ry) {}
    
    D2D1_ROUNDED_RECT ToD2D() const {
        return D2D1::RoundedRect(rect.ToD2D(), radiusX, radiusY);
    }
};


struct Ellipse {
    Point center;
    float radiusX, radiusY;
    
    Ellipse() : center(0, 0), radiusX(0), radiusY(0) {}
    Ellipse(const Point& c, float rx, float ry) : center(c), radiusX(rx), radiusY(ry) {}
    Ellipse(float cx, float cy, float rx, float ry) : center(cx, cy), radiusX(rx), radiusY(ry) {}
    Ellipse(float cx, float cy, float r) : center(cx, cy), radiusX(r), radiusY(r) {}
    
    D2D1_ELLIPSE ToD2D() const {
        return D2D1::Ellipse(D2D1::Point2F(center.x, center.y), radiusX, radiusY);
    }
};

} 