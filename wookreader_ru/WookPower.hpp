#pragma once

#include <cstdint>

namespace wookpower {

// Idle loops sleep 16 ms between polling input and asynchronous results. An
// unchanged reader/library keeps the last presented frame instead of redrawing it.
constexpr std::int64_t idle_sleep_ns = 16000000;

class FrameGate {
    bool dirty_ = true;
public:
    void invalidate(bool changed = true) { dirty_ = dirty_ || changed; }
    bool take(bool battery_saver) {
        if (battery_saver && !dirty_) return false;
        dirty_ = false;
        return true;
    }
};

// RARFIX2 already limited its three allocated slots to two active workers.
// Preserve that normal profile; the saver prepares only the next page.
constexpr int prefetch_slots(bool battery_saver) { return battery_saver ? 1 : 2; }

} // namespace wookpower

#ifdef __SWITCH__
#include <SDL2/SDL.h>

namespace wookpower {

// The main loop owns SDL_PollEvent (including keyboard input). A predicate
// avoids draining that queue a second time and accidentally discarding input.
inline bool window_event(const SDL_Event &event) {
    switch (event.type) {
    case SDL_WINDOWEVENT:
    case SDL_APP_DIDENTERFOREGROUND:
    case SDL_RENDER_TARGETS_RESET:
    case SDL_RENDER_DEVICE_RESET:
        return true;
    default:
        return false;
    }
}

} // namespace wookpower
#endif
