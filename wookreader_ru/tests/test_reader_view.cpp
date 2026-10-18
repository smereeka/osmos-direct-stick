#include "ReaderView.hpp"
#include <cassert>
#include <cstdio>
#include <limits>

int main() {
    // The same content position must survive changed page dimensions, viewport
    // orientation, and a save/restore cycle at either end and in the middle.
    for (float viewport : {720.0f, 1280.0f}) {
        for (float extent : {500.0f, 720.0f, 1600.0f, 4096.0f}) {
            for (float pan : {0.0f, 0.25f, 0.5f, 0.9f, 1.0f}) {
                float center = reader_restore_pan(pan, extent, viewport);
                float saved = reader_save_pan(center, extent, viewport);
                assert(std::abs(saved - (extent > viewport ? pan : 0.0f)) < 0.00001f);
                assert(reader_center(center, extent, viewport) == center);
                assert(reader_center(-1e6f, extent, viewport) >= 0.0f - extent);
                if (extent > viewport) {
                    float bigger = extent * 1.7f;
                    float moved = reader_restore_pan(saved, bigger, viewport);
                    assert(std::abs(reader_save_pan(moved, bigger, viewport) - pan) < 0.00001f);
                }
            }
        }
    }
    assert(reader_restore_pan(-10, 2000, 1000) == 1000);
    assert(reader_restore_pan(10, 2000, 1000) == 0);
    assert(reader_unit(std::numeric_limits<float>::quiet_NaN()) == 0);
    int mode = 0;
    for (int cycle = 0; cycle < 5; ++cycle) {
        assert(!reader_rotated(mode));
        mode = reader_next_mode(mode); assert(mode == 1 && reader_rotated(mode));
        mode = reader_next_mode(mode); assert(mode == 2 && !reader_rotated(mode));
        mode = reader_next_mode(mode); assert(mode == 0 && !reader_rotated(mode));
    }
    assert(reader_last_visible(0, 1, true) == 0);
    assert(reader_last_visible(2, 4, true) == 3);
    assert(reader_last_visible(2, 4, false) == 2);
    assert(reader_last_visible(4, 5, true) == 4);
    assert(reader_last_visible(0, 0, false) == -1);
    std::puts("reader view: size/orientation round trips, clamps, mode cycle, spread end: PASS");
}
