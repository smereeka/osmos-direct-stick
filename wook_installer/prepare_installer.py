#!/usr/bin/env python3
"""Prepare a WOOK-only on-console installer using a pinned HomeTile backend.
The reader NRO and icon are inserted in RomFS during final packaging.
No reader code, comic library, or settings are modified by this builder.
"""
from pathlib import Path
import sys
root=Path(sys.argv[1])
p=root/'native/source/main.cpp'
s=p.read_text(encoding='utf-8')

def once(old,new):
    global s
    assert s.count(old)==1, (old[:100],s.count(old))
    s=s.replace(old,new,1)

# Correct UTF-8 drawing using the existing console-provided shared font.
utf8=r'''
std::vector<uint32_t> wookCodepoints(const std::string& s) {
    std::vector<uint32_t> out;
    for (size_t i=0;i<s.size();) {
        unsigned char b=(unsigned char)s[i++];
        uint32_t cp=b; int more=0;
        if (b>=0xC2 && b<=0xDF) { cp=b&31;more=1; }
        else if (b>=0xE0 && b<=0xEF) { cp=b&15;more=2; }
        else if (b>=0xF0 && b<=0xF4) { cp=b&7;more=3; }
        else if (b>=0x80) { out.push_back('?');continue; }
        bool ok=true;
        for(int k=0;k<more;k++) {
            if(i>=s.size() || ((unsigned char)s[i]&0xC0)!=0x80) {ok=false;break;}
            cp=(cp<<6)|((unsigned char)s[i++]&63);
        }
        out.push_back(ok?cp:'?');
    }
    return out;
}
'''
s=s.replace('int textWidth(FT_Face face,',utf8+'\nint textWidth(FT_Face face,',1)
s=s.replace('for (unsigned char c : s) {','for (uint32_t c : wookCodepoints(s)) {')
s=s.replace('for (unsigned char c : text) {','for (uint32_t c : wookCodepoints(text)) {')
s=s.replace('if (c < 0x20 || c >= 0x80) continue;','if (c < 0x20) continue;')
s=s.replace('while (!out.empty() && textWidth(face, out + "...", px) > maxW) out.pop_back();',
'''while (!out.empty() && textWidth(face, out + "...", px) > maxW) {
        size_t at=out.size()-1;
        while(at && ((unsigned char)out[at]&0xC0)==0x80) --at;
        out.resize(at);
    }''')
# Separate registry; do not take ownership of unrelated HOME entries.
s=s.replace('sdmc:/config/hometile','sdmc:/config/wook-installer')
# Fail closed when checking installed title ownership, rather than assuming absence.
old='''    Result rc = nsInitialize();
    if (R_SUCCEEDED(rc)) {
        rc = nsIsAnyApplicationEntityInstalled(tid, &installed);
        nsExit();
        if (R_FAILED(rc)) installed = false;
    }
    if (installed && !registryOwnsTitle(app.path, tid)) {'''
new='''    Result rc = nsInitialize();
    if (R_FAILED(rc)) {
        g_notice="Не удалось открыть службу установки. Запустите через R + игра.";
        g_notice_error=true; return false;
    }
    rc = nsIsAnyApplicationEntityInstalled(tid, &installed);
    nsExit();
    if (R_FAILED(rc)) {
        g_notice="Не удалось проверить HOME. Установка остановлена без замены игр.";
        g_notice_error=true; return false;
    }
    if (installed && !registryOwnsTitle(app.path, tid)) {'''
once(old,new)
# Use a WOOK-specific deterministic title identity, separate from other forwarders.
# The backend applies the SAME salt (patched below).
old='const std::string hash_path = path + path;'
if old in s:
    s=s.replace(old,'const std::string hash_path = path + path + "|WOOK_ICON_RU2";',1)
else:
    # This string is verified by the build assertions below, not guessed silently.
    old='const auto hash_path = path + path;'
    assert old in s, 'Need to inspect title-id helper'
    s=s.replace(old,'const auto hash_path = path + path + "|WOOK_ICON_RU2";',1)

# Remove the generic launcher main; expose only one embedded WOOK installer.
marker='} // namespace\n\nint main('
pos=s.rfind(marker)
assert pos>0
s=s[:pos]+r'''
constexpr const char* WOOK_PAYLOAD="romfs:/payload/WookReader.nro";
constexpr const char* WOOK_TARGET="sdmc:/switch/WookReader/WookReader.nro";

bool writeWholeFile(const char* path,const std::vector<u8>& data) {
    FILE* f=fopen(path,"wb");
    if(!f) return false;
    const bool written=fwrite(data.data(),1,data.size(),f)==data.size();
    const bool closed=fclose(f)==0;
    return written && closed;
}

bool deployWook(AppEntry& app) {
    std::vector<u8> payload;
    if(!readFileVec(WOOK_PAYLOAD,payload,64*1024*1024) || payload.size()<0x80 ||
       read32le(payload.data()+0x10)!=0x304f524e) {
        g_notice="Повреждён установочный файл WOOK. Книги не изменены.";
        g_notice_error=true;return false;
    }
    std::ifstream manifest("romfs:/payload/sha256.txt");
    std::string expected;manifest>>expected;
    if(expected.size()!=64) {
        g_notice="Нет контрольной суммы WOOK. Установка остановлена.";
        g_notice_error=true;return false;
    }
    u8 digest[32]{};sha256CalculateHash(digest,payload.data(),payload.size());
    char actual[65]{};
    for(int i=0;i<32;i++) snprintf(actual+i*2,3,"%02x",digest[i]);
    if(expected!=actual) {
        g_notice="Проверка WOOK не пройдена. Скопируйте установщик заново.";
        g_notice_error=true;return false;
    }
    ensureDir("sdmc:/switch");ensureDir("sdmc:/switch/WookReader");
    ensureDir("sdmc:/switch/WookReader/books");
    std::vector<u8> current;
    bool same=readFileVec(WOOK_TARGET,current,64*1024*1024) && current==payload;
    current.clear();current.shrink_to_fit();
    if(!same) {
        const std::string temp=std::string(WOOK_TARGET)+".installing";
        if(!writeWholeFile(temp.c_str(),payload)) {
            remove(temp.c_str());
            g_notice="Не удалось записать WOOK на SD. Проверьте свободное место.";
            g_notice_error=true;return false;
        }
        std::string backup;
        if(exists(WOOK_TARGET)) {
            for(unsigned i=1;i<1000;i++) {
                std::string candidate=std::string(WOOK_TARGET)+".backup-"+std::to_string(i);
                if(!exists(candidate.c_str())) {backup=candidate;break;}
            }
            if(backup.empty() || rename(WOOK_TARGET,backup.c_str())!=0) {
                remove(temp.c_str());
                g_notice="Не удалось сохранить предыдущий WookReader. Замена отменена.";
                g_notice_error=true;return false;
            }
        }
        if(rename(temp.c_str(),WOOK_TARGET)!=0) {
            if(!backup.empty()) rename(backup.c_str(),WOOK_TARGET);
            remove(temp.c_str());
            g_notice="Не удалось завершить копирование. Проверьте папку WookReader.";
            g_notice_error=true;return false;
        }
    }
    app={};app.path=WOOK_TARGET;
    if(!parseNroMetadata(app)) {
        g_notice="Не удалось прочитать установленный WOOK.";
        g_notice_error=true;return false;
    }
    app.parse_ok=true;
    return true;
}

void renderWookInstaller(Graphics& gfx,Font& font,IconCache& icons,
                         const AppEntry& app,bool confirm_install,bool success,bool gate) {
    u32* fb=gfx.begin();int stride=gfx.stride();FT_Face face=font.face();
    fillRect(fb,stride,0,0,FB_W,FB_H,COL_BG);
    const Image* icon=icons.get(app);
    if(icon && icon->valid()) drawImage(fb,stride,*icon,48,105,400,400);
    drawText(fb,stride,face,495,120,48,COL_TEXT,"WOOK");
    drawText(fb,stride,face,495,163,22,COL_ACCENT,"Русская читалка комиксов • RU2");
    if(gate) {
        drawText(fb,stride,face,495,238,26,COL_YELLOW,"Нужен режим полной памяти");
        drawText(fb,stride,face,495,287,21,COL_TEXT,"Выйдите на HOME. Удерживайте R");
        drawText(fb,stride,face,495,323,21,COL_TEXT,"при запуске любой установленной игры.");
        drawText(fb,stride,face,495,359,21,COL_TEXT,"В Homebrew Menu откройте WOOK Installer.");
    } else if(success) {
        drawText(fb,stride,face,495,238,30,COL_GREEN,"WOOK установлен");
        drawText(fb,stride,face,495,286,21,COL_TEXT,"Новая иконка добавлена в меню HOME.");
        drawText(fb,stride,face,495,324,21,COL_TEXT,"Книги, заметки и настройки сохранены.");
        drawText(fb,stride,face,495,370,23,COL_ACCENT,"Нажмите +, затем вернитесь на HOME.");
    } else {
        drawText(fb,stride,face,495,233,27,COL_TEXT,
            confirm_install?"Подтвердить установку?":"Установка программы и HOME-иконки");
        drawText(fb,stride,face,495,280,21,COL_TEXT,"Установщик сам скопирует WOOK на SD");
        drawText(fb,stride,face,495,316,21,COL_TEXT,"и создаст значок с этой картинкой.");
        drawText(fb,stride,face,495,367,20,COL_MUTED,"Ваши книги и настройки не удаляются.");
        drawText(fb,stride,face,495,403,20,COL_MUTED,"Предыдущая программа останется в копии.");
        fillRoundedRect(fb,stride,495,452,670,62,14,C(32,69,95));
        drawText(fb,stride,face,520,492,24,COL_TEXT,
            confirm_install?"A — подтвердить     B — отмена":"A — начать установку");
    }
    if(!g_notice.empty()) {
        drawText(fb,stride,face,48,575,20,g_notice_error?COL_RED:COL_GREEN,
            ellipsize(face,g_notice,20,1170));
    }
    drawText(fb,stride,face,48,647,17,COL_MUTED,
        "Устанавливайте только в используемой вами среде Atmosphere / CFW.");
    drawText(fb,stride,face,48,683,17,COL_MUTED,"+ — выход. После установки WOOK открывается прямо с HOME.");
    gfx.end();
}
} // namespace

int main(int,char**) {
    PadState pad;padConfigureInput(1,HidNpadStyleSet_NpadStandard);padInitializeDefault(&pad);
    Font font;Graphics gfx;
    if(!font.init() || !gfx.init()) return 1;
    Result romfs_rc=romfsInit();
    AppEntry preview{};preview.path=WOOK_PAYLOAD;
    bool valid=R_SUCCEEDED(romfs_rc) && parseNroMetadata(preview);
    if(!valid) {g_notice="Нет файлов WOOK в установщике. Установка недоступна.";g_notice_error=true;}
    IconCache icons;bool confirm_install=false,success=false;
    bool gate=appletGetAppletType()!=AppletType_Application;
    renderWookInstaller(gfx,font,icons,preview,confirm_install,success,gate);
    while(appletMainLoop()) {
        padUpdate(&pad);u64 down=padGetButtonsDown(&pad);
        if(down&HidNpadButton_Plus) break;
        if(down&HidNpadButton_B) {
            if(confirm_install) confirm_install=false;else break;
        }
        if((down&HidNpadButton_A) && valid && !gate && !success) {
            if(!confirm_install) {confirm_install=true;g_notice.clear();}
            else {
                g_notice="Проверка и копирование WOOK...";g_notice_error=false;
                renderWookInstaller(gfx,font,icons,preview,true,false,false);
                AppEntry installed{};
                if(deployWook(installed)) success=installNativeForwarder(gfx,font,installed);
                confirm_install=false;
            }
        }
        if(down) renderWookInstaller(gfx,font,icons,preview,confirm_install,success,gate);
        svcSleepThread(8000000);
    }
    icons.clear();
    if(R_SUCCEEDED(romfs_rc)) romfsExit();
    return 0;
}
'''
# Brand the small native progress screen too; no arbitrary app scan is reachable.
s=s.replace('"HomeTile"','"WOOK Installer"')
s=s.replace('"ADDING TO HOME"','"УСТАНОВКА ИКОНКИ HOME"')
p.write_text(s,encoding='utf-8')
# Salt must agree with deterministicTitleId in the UI collision guard.
v=root/'native/vendor/owo.cpp'
t=v.read_text(encoding='utf-8')
old='const auto hash_path = config.nro_path + config.args;'
assert t.count(old)==1
t=t.replace(old,'const auto hash_path = config.nro_path + config.args + "|WOOK_ICON_RU2";',1)
v.write_text(t,encoding='utf-8')
m=root/'native/Makefile'
t=m.read_text(encoding='utf-8')
t=t.replace('TARGET      := HomeTile','TARGET      := WOOK_Installer')
t=t.replace('APP_TITLE   := HomeTile','APP_TITLE   := WOOK Installer')
t=t.replace('APP_AUTHOR  := HomeTile Project','APP_AUTHOR  := HomeTile / WOOK personal build')
t=t.replace('APP_VERSION := 0.6.3','APP_VERSION := 1.0.0-WOOK')
m.write_text(t,encoding='utf-8')
print('Prepared WOOK-only installer. Payload must be added before distribution.')
