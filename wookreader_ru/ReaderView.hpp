#pragma once

#include <algorithm>
#include <cmath>

// View state uses a zoom ratio relative to the current fit and a 0..1 scroll
// position. It remains meaningful when successive pages have different sizes.
struct ReaderView {
    float zoom = 1.0f;
    float pan_x = 0.0f;
    float pan_y = 0.0f;
};

inline float reader_unit(float value) {
    return std::isfinite(value) ? std::max(0.0f, std::min(value, 1.0f)) : 0.0f;
}

inline float reader_center(float center, float extent, float viewport) {
    if (extent <= viewport) return viewport * 0.5f;
    return std::max(viewport - extent * 0.5f, std::min(center, extent * 0.5f));
}

inline float reader_save_pan(float center, float extent, float viewport) {
    return extent > viewport ? reader_unit((extent * 0.5f - center) / (extent - viewport)) : 0.0f;
}

inline float reader_restore_pan(float value, float extent, float viewport) {
    return extent > viewport ? extent * 0.5f - reader_unit(value) * (extent - viewport) : viewport * 0.5f;
}

inline bool reader_rotated(int mode) { return mode == 1; }
inline int reader_next_mode(int mode) { return mode >= 0 && mode < 2 ? mode + 1 : 0; }
inline int reader_last_visible(int page, int total, bool spread) {
    return total > 0 ? std::min(total - 1, std::max(0, page) + (spread ? 1 : 0)) : -1;
}
