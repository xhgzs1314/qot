#pragma once
#include <functional>
#include <chrono>
#include <vector>
#include <memory>
#include <algorithm>
#include <cmath>
#ifdef min
#undef min
#endif
#ifdef max
#undef max
#endif
namespace gui {
enum class EasingType {
    Linear,
    QuadIn, QuadOut, QuadInOut,
    CubicIn, CubicOut, CubicInOut,
    SineIn, SineOut, SineInOut,
    ElasticOut,
    BounceOut
};
class Animation {
public:
    using UpdateCallback = std::function<void(float value)>;
    
    Animation();
    
    void Start(float from, float to, float duration, EasingType easing = EasingType::QuadOut);
    void Update(float deltaTime);
    bool IsPlaying() const { return m_isPlaying; }
    void Stop();
    float GetValue() const { return m_currentValue; }
    void OnUpdate(UpdateCallback callback) { m_callback = callback; }
    
private:
    float EvaluateEasing(float t) const;
    
    bool m_isPlaying;
    float m_fromValue;
    float m_toValue;
    float m_duration;
    float m_elapsedTime;
    float m_currentValue;
    EasingType m_easing;
    UpdateCallback m_callback;
};
class AnimationManager {
public:
    static AnimationManager& Instance();
    
    void Update(float deltaTime);
    Animation* CreateAnimation();
    void RemoveAnimation(Animation* anim);
    
private:
    AnimationManager() = default;
    std::vector<std::unique_ptr<Animation>> m_animations;
};
inline float EasingLinear(float t) { return t; }

inline float EasingQuadIn(float t) { return t * t; }
inline float EasingQuadOut(float t) { return t * (2 - t); }
inline float EasingQuadInOut(float t) { 
    if (t < 0.5f) return 2 * t * t;
    return -1 + (4 - 2 * t) * t;
}

inline float EasingCubicIn(float t) { return t * t * t; }
inline float EasingCubicOut(float t) { 
    float t1 = t - 1;
    return t1 * t1 * t1 + 1;
}
inline float EasingCubicInOut(float t) { 
    if (t < 0.5f) return 4 * t * t * t;
    return (t - 1) * (2 * t - 2) * (2 * t - 2) + 1;
}

inline float EasingSineIn(float t) { return 1 - cosf(t * 3.14159f / 2); }
inline float EasingSineOut(float t) { return sinf(t * 3.14159f / 2); }
inline float EasingSineInOut(float t) { return (1 - cosf(3.14159f * t)) / 2; }

inline float EasingElasticOut(float t) {
    if (t == 0 || t == 1) return t;
    return powf(2, -10 * t) * sinf((t - 0.075f) * (2 * 3.14159f) / 0.3f) + 1;
}

inline float EasingBounceOut(float t) {
    if (t < 1 / 2.75f) return 7.5625f * t * t;
    if (t < 2 / 2.75f) { 
        float t1 = t - 1.5f / 2.75f;
        return 7.5625f * t1 * t1 + 0.75f;
    }
    if (t < 2.5f / 2.75f) { 
        float t1 = t - 2.25f / 2.75f;
        return 7.5625f * t1 * t1 + 0.9375f;
    }
    float t1 = t - 2.625f / 2.75f;
    return 7.5625f * t1 * t1 + 0.984375f;
}
inline Animation::Animation()
    : m_isPlaying(false)
    , m_fromValue(0)
    , m_toValue(0)
    , m_duration(0)
    , m_elapsedTime(0)
    , m_currentValue(0)
    , m_easing(EasingType::Linear) {
}

inline void Animation::Start(float from, float to, float duration, EasingType easing) {
    m_fromValue = from;
    m_toValue = to;
    m_duration = duration;
    m_elapsedTime = 0;
    m_easing = easing;
    m_isPlaying = true;
    m_currentValue = from;
}

inline void Animation::Update(float deltaTime) {
    if (!m_isPlaying) return;
    
    m_elapsedTime += deltaTime;
    float t = std::min(1.0f, m_elapsedTime / m_duration);
    
    float easedT = EvaluateEasing(t);
    m_currentValue = m_fromValue + (m_toValue - m_fromValue) * easedT;
    
    if (m_callback) {
        m_callback(m_currentValue);
    }
    
    if (t >= 1.0f) {
        m_isPlaying = false;
    }
}

inline void Animation::Stop() {
    m_isPlaying = false;
}

inline float Animation::EvaluateEasing(float t) const {
    switch (m_easing) {
        case EasingType::Linear: return EasingLinear(t);
        case EasingType::QuadIn: return EasingQuadIn(t);
        case EasingType::QuadOut: return EasingQuadOut(t);
        case EasingType::QuadInOut: return EasingQuadInOut(t);
        case EasingType::CubicIn: return EasingCubicIn(t);
        case EasingType::CubicOut: return EasingCubicOut(t);
        case EasingType::CubicInOut: return EasingCubicInOut(t);
        case EasingType::SineIn: return EasingSineIn(t);
        case EasingType::SineOut: return EasingSineOut(t);
        case EasingType::SineInOut: return EasingSineInOut(t);
        case EasingType::ElasticOut: return EasingElasticOut(t);
        case EasingType::BounceOut: return EasingBounceOut(t);
        default: return t;
    }
}

inline AnimationManager& AnimationManager::Instance() {
    static AnimationManager instance;
    return instance;
}

inline void AnimationManager::Update(float deltaTime) {
    for (auto& anim : m_animations) {
        anim->Update(deltaTime);
    }
    m_animations.erase(
        std::remove_if(m_animations.begin(), m_animations.end(),
            [](const std::unique_ptr<Animation>& a) { return !a->IsPlaying(); }),
        m_animations.end());
}

inline Animation* AnimationManager::CreateAnimation() {
    auto anim = std::make_unique<Animation>();
    Animation* ptr = anim.get();
    m_animations.push_back(std::move(anim));
    return ptr;
}

inline void AnimationManager::RemoveAnimation(Animation* anim) {
    auto it = std::find_if(m_animations.begin(), m_animations.end(),
        [anim](const std::unique_ptr<Animation>& a) { return a.get() == anim; });
    if (it != m_animations.end()) {
        m_animations.erase(it);
    }
}

} 