#!/usr/bin/env python3
"""Replay the produced chooser with real SDL/TTF and synthetic books on host.

The chooser helpers, layout, input loop, menus and state calls are extracted
unchanged. Only absolute storage prefixes, the final observation hook, libnx
services, cover pixels and the reader entry point are adapted for a host test.
This is neither a Horizon execution test nor a test of image/archive decoding.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def function(text, signature):
    start = text.index(signature)
    opening = text.index("{", start)
    depth, pos = 1, opening + 1
    while depth:
        if text.startswith("//", pos):
            pos = text.index("\n", pos) + 1
            continue
        if text.startswith("/*", pos):
            pos = text.index("*/", pos) + 2
            continue
        if text[pos] in "\"'":
            quote = text[pos]
            pos += 1
            while text[pos] != quote:
                pos += 2 if text[pos] == "\\" else 1
            pos += 1
            continue
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    return text[start:pos]


PRELUDE = r'''
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <libconfig.h>
#include <bits/stdc++.h>
#include <sys/stat.h>
#include "WookLibrary.hpp"
using namespace std;
namespace fs = std::filesystem;
using u64=uint64_t; using Result=uint32_t;
#define R_SUCCEEDED(r) ((r)==0)
#define R_FAILED(r) ((r)!=0)
struct Thread { int slot=-1; };
struct PadState {};
struct HidTouchScreenState { unsigned count=0; struct { unsigned x=0,y=0; } touches[16]; };
struct SwkbdConfig {};
enum {HidNpadStyleSet_NpadStandard, SwkbdType_Normal};
constexpr u64 HidNpadButton_A=1ULL<<0, HidNpadButton_B=1ULL<<1,
 HidNpadButton_X=1ULL<<2, HidNpadButton_Y=1ULL<<3,
 HidNpadButton_L=1ULL<<4, HidNpadButton_R=1ULL<<5,
 HidNpadButton_ZL=1ULL<<6, HidNpadButton_ZR=1ULL<<7,
 HidNpadButton_Left=1ULL<<8, HidNpadButton_Right=1ULL<<9,
 HidNpadButton_Up=1ULL<<10, HidNpadButton_Down=1ULL<<11,
 HidNpadButton_Plus=1ULL<<12, HidNpadButton_Minus=1ULL<<13,
 HidNpadButton_StickRUp=1ULL<<14, HidNpadButton_StickRDown=1ULL<<15,
 HidNpadButton_StickRLeft=1ULL<<16, HidNpadButton_StickRRight=1ULL<<17;
static SDL_Window* WINDOW=nullptr;
static SDL_Renderer* RENDERER=nullptr;
static TTF_Font *ROBOTO_15, *ROBOTO_20, *ROBOTO_25, *ROBOTO_30, *ROBOTO_35;
static bool configDarkMode=true, configScreenButtons=true, configStatusBar=true;
#define WHITE SDL_Color{255,255,255,255}
#define BLACK SDL_Color{0,0,0,255}
#define BACK_BLACK SDL_Color{30,30,30,255}
#define BACK_WHITE SDL_Color{200,200,200,255}
#define SELECTOR_COLOUR_DARK SDL_Color{76,76,76,255}
#define SELECTOR_COLOUR_LIGHT SDL_Color{220,220,220,255}
#define HINT_COLOUR_DARK SDL_Color{70,70,70,255}
#define HINT_COLOUR_LIGHT SDL_Color{210,210,210,255}
static u64 host_buttons=0;
static HidTouchScreenState host_touch;
static vector<string> opened_books;
static vector<string> opened_sequences;
static deque<string> keyboard_answers;
static vector<string> errors;
static string host_root, screenshot_dir;
static int checks=0;
static void check(bool passed,const string& label) {
  if (!passed) { cerr<<"FAIL: "<<label<<"\n"; std::exit(2); }
  ++checks;
}
static void Log_Write(const string&) {}
static void Log_Error(const string& text) { errors.push_back(text); }
static void FS_RecursiveMakeDir(const char* path) { std::error_code ec; fs::create_directories(path,ec); }
static bool FS_FileExists(const char* path) { std::error_code ec; return fs::is_regular_file(path,ec); }
static void SDL_ClearScreen(SDL_Renderer* r,SDL_Color c) { SDL_SetRenderDrawColor(r,c.r,c.g,c.b,c.a); }
static void SDL_DrawRect(SDL_Renderer* r,int x,int y,int w,int h,SDL_Color c) {
  SDL_SetRenderDrawColor(r,c.r,c.g,c.b,c.a); SDL_Rect rect{x,y,w,h}; SDL_RenderFillRect(r,&rect);
}
static void SDL_DrawImageScale(SDL_Renderer* r,SDL_Texture* texture,int x,int y,int w,int h) {
  if(texture) { SDL_Rect rect{x,y,w,h}; SDL_RenderCopy(r,texture,nullptr,&rect); }
}
static void SDL_TextCache_NextFrame() {}
static void padConfigureInput(int,int) {}
static void padInitializeDefault(PadState*) {}
static void hidInitializeTouchScreen() {}
static void padUpdate(PadState*) {}
static u64 padGetButtonsDown(PadState*) { return host_buttons; }
static u64 padGetButtonsUp(PadState*) { return 0; }
static u64 padGetButtons(PadState*) { return host_buttons; }
static void hidGetTouchScreenStates(HidTouchScreenState* state,int) { *state=host_touch; }
static bool appletMainLoop();
static Result swkbdCreate(SwkbdConfig*,int) { return 0; }
static void swkbdClose(SwkbdConfig*) {}
static Result swkbdShow(SwkbdConfig*,char* output,size_t size) {
  check(!keyboard_answers.empty(),"software keyboard has an explicit test answer");
  string value=keyboard_answers.front(); keyboard_answers.pop_front();
  check(value.size()<size,"keyboard buffer retains all UTF-8 bytes");
  memcpy(output,value.c_str(),value.size()+1); return 0;
}
static void swkbdConfigSetType(SwkbdConfig*,int) {}
static void swkbdConfigSetHeaderText(SwkbdConfig*,const char*) {}
static void swkbdConfigSetStringLenMax(SwkbdConfig*,int) {}
static void swkbdConfigSetStringLenMin(SwkbdConfig*,int) {}
static void swkbdConfigSetBlurBackground(SwkbdConfig*,int) {}
static void swkbdConfigSetInitialText(SwkbdConfig*,const char*) {}
static void swkbdConfigSetInitialCursorPos(SwkbdConfig*,int) {}
static void Menu_OpenBook(char* path,int,float) {
  opened_books.emplace_back(path); opened_sequences.push_back(wook::reading_sequence_name());
  wook::save_progress(path,11,25); check(wook::flush(),"reader test progress saved");
}
struct TestThread { void(*run)(void*)=nullptr; void* arg=nullptr; std::thread thread; };
static vector<unique_ptr<TestThread>> host_threads;
static Result threadCreate(Thread* thread,void(*run)(void*),void* arg,void*,size_t,int,int) {
  thread->slot=host_threads.size(); auto t=make_unique<TestThread>(); t->run=run;t->arg=arg;
  host_threads.push_back(std::move(t)); return 0;
}
static Result threadStart(Thread* t) { auto* p=host_threads[t->slot].get(); p->thread=std::thread(p->run,p->arg);return 0; }
static void threadWaitForExit(Thread* t) { host_threads[t->slot]->thread.join(); }
static void threadClose(Thread*) {}
static void svcSleepThread(int64_t ns) { std::this_thread::sleep_for(std::chrono::nanoseconds(ns)); }
static unsigned char* extract_cbz_cover_pixels(const char* path,int tw,int th,int* iw,int* ih) {
  // Synthetic color cards; the actual decoder regression is a separate test.
  if(string(path).find("Хранители")!=string::npos) return nullptr;
  auto* p=static_cast<unsigned char*>(malloc(static_cast<size_t>(tw)*th*4));
  if(!p) return nullptr; *iw=tw;*ih=th;
  unsigned seed=0;for(unsigned char c:string(path))seed=seed*31+c;
  for(int y=0;y<th;++y)for(int x=0;x<tw;++x) {
    auto* q=p+(static_cast<size_t>(y)*tw+x)*4;
    q[0]=35+(seed%130)+x*30/std::max(1,tw);q[1]=45+((seed>>4)%120);q[2]=70+((seed>>9)%100)+y*20/std::max(1,th);q[3]=255;
  }
  return p;
}
static unsigned char* extract_pdf_cover_pixels(const char* p,int w,int h,int* iw,int* ih) { return extract_cbz_cover_pixels(p,w,h,iw,ih); }
static SDL_Texture* pixels_to_texture(unsigned char* pixels,int w,int h,const char*) {
  if(!pixels)return nullptr;
  SDL_Surface* surface=SDL_CreateRGBSurfaceWithFormatFrom(pixels,w,h,32,w*4,SDL_PIXELFORMAT_RGBA32);
  SDL_Texture* texture=surface?SDL_CreateTextureFromSurface(RENDERER,surface):nullptr;
  SDL_FreeSurface(surface);free(pixels);return texture;
}
'''


DRIVER = r'''
struct Snapshot {
 LibraryView view; vector<LibraryEntry> entries; LibraryModal modal=LibraryModal::None;
 vector<string> actions,picker; int index=0, modal_index=0, modal_scroll=0, cols=5;
 string modal_text; vector<uint8_t> failed;
} snap;
struct Command { int kind; string name,answer; u64 buttons=0; std::function<void()> verify; int attempts=0; bool initialized=false; };
static deque<Command> commands;
static vector<string> screenshots;
static int frame_count=0;
static string pending_screenshot;
static void observe(const LibraryView& view,const vector<LibraryEntry>& entries,LibraryModal modal,
 const vector<LibraryAction>& actions,const vector<string>& picker,int index,int mi,int ms,int cols,
 const string& text,const vector<uint8_t>& failed) {
 snap={view,entries,modal,{},picker,index,mi,ms,cols,text,failed};
 for(const auto& action:actions)snap.actions.push_back(action.label);
 if(!pending_screenshot.empty()) {
  SDL_Surface* image=SDL_CreateRGBSurfaceWithFormat(0,1280,720,32,SDL_PIXELFORMAT_RGBA32);
  check(image&&SDL_RenderReadPixels(RENDERER,nullptr,SDL_PIXELFORMAT_RGBA32,image->pixels,image->pitch)==0,"actual SDL framebuffer readable");
  const string file=screenshot_dir+"/"+pending_screenshot+".bmp";
  check(SDL_SaveBMP(image,file.c_str())==0,"actual UI framebuffer saved");SDL_FreeSurface(image);
  screenshots.push_back(file);pending_screenshot.clear();
 }
}
static bool appletMainLoop() {
 if(host_buttons){host_buttons=0;host_touch={};++frame_count;return true;}
 host_buttons=0;host_touch={};
 if(++frame_count>1600){cerr<<"FAIL: UI replay frame limit\n";return false;}
 if(commands.empty())return false;
 auto& cmd=commands.front();
 if(++cmd.attempts>100){cerr<<"FAIL: command timeout "<<cmd.name<<" modal="<<(int)snap.modal<<"\n";exit(2);}
 if(!cmd.initialized){cmd.initialized=true;if(cmd.kind==4)keyboard_answers.push_back(cmd.answer);}
 if(cmd.kind==0){host_buttons=cmd.buttons;commands.pop_front();return true;}
 if(cmd.kind==1){auto verify=cmd.verify;commands.pop_front();verify();return true;}
 if(cmd.kind==2){pending_screenshot=cmd.name;commands.pop_front();return true;}
 if(cmd.kind==3||cmd.kind==4){
  if(snap.modal==LibraryModal::None){host_buttons=HidNpadButton_X;return true;}
  check(snap.modal==LibraryModal::Actions||snap.modal==LibraryModal::Options,"action menu is exclusive");
  int target=-1;
  for(size_t i=0;i<snap.actions.size();++i)if(snap.actions[i].rfind(cmd.name,0)==0){target=i;break;}
  check(target>=0,"menu action exists: "+cmd.name);
  if(target!=snap.modal_index){host_buttons=HidNpadButton_Down;return true;}
  host_buttons=HidNpadButton_A;commands.pop_front();return true;
 }
 if(cmd.kind==5){
  check(snap.modal==LibraryModal::None,"selection stays in library");
  int target=-1;for(size_t i=0;i<snap.entries.size();++i)if(snap.entries[i].name==cmd.name){target=i;break;}
  check(target>=0,"library entry exists: "+cmd.name);
  if(target!=snap.index){host_buttons=HidNpadButton_Right;return true;}
  commands.pop_front();return true;
 }
 if(cmd.kind==6){
  check(snap.modal==LibraryModal::Picker,"member picker shown");
  int target=-1;for(size_t i=0;i<snap.picker.size();++i)if(wook::title(snap.picker[i])==cmd.name){target=i;break;}
  check(target>=0,"member picker original path present");
  if(target!=snap.modal_index){host_buttons=HidNpadButton_Down;return true;}
  host_buttons=HidNpadButton_A;commands.pop_front();return true;
 }
 if(cmd.kind==7){keyboard_answers.push_back(cmd.answer);host_buttons=cmd.buttons;commands.pop_front();return true;}
 return true;
}
static void press(u64 b){commands.push_back({0,"button","",b,{}});}
static void verify(std::function<void()> fn){commands.push_back({1,"verify","",0,fn});}
static void screenshot(string name){commands.push_back({2,name,"",0,{}});}
static void menu(string name){commands.push_back({3,name,"",0,{}});}
static void menu_keyboard(string name,string answer){commands.push_back({4,name,answer,0,{}});}
static void select(string name){commands.push_back({5,name,"",0,{}});}
static void pick(string name){commands.push_back({6,name,"",0,{}});}
static void answer(u64 b,string value){commands.push_back({7,"answer",value,b,{}});}
'''


MAIN = r'''
static wook::Collection group_named(const string& name) {
 for(const auto& c:wook::collections())if(c.name==name)return c;
 return {};
}
static string read_file(const string& path){ifstream f(path);return{istreambuf_iterator<char>(f),{}};}
int main(int argc,char** argv) {
 check(argc==4,"host test arguments");host_root=argv[1];screenshot_dir=argv[3];
 fs::create_directories(host_root+"/books/Серия");fs::create_directories(host_root+"/switch/WookReader/.notes");fs::create_directories(screenshot_dir);
 const string app=host_root+"/switch/WookReader", books=host_root+"/books";
 const string watchmen=books+"/Хранители.cbr";
 const string issue1=books+"/Серия/Рик и Морти 001.cbr",issue2=books+"/Серия/Рик и Морти 002.cbr",issue10=books+"/Серия/Рик и Морти 010.cbr";
 const string long_title="Очень длинное русское название коллекционного выпуска с подробным подзаголовком и продолжением истории 053";
 for(const auto& file:vector<string>{watchmen,issue1,issue2,issue10,books+"/"+long_title+".cbr",books+"/123 Комикс.cbz",books+"/Another book.pdf"})ofstream(file)<<"synthetic UI fixture\n";
 wook::set_storage_root_for_tests(app);wook::initialize();
 ofstream(app+"/options.cfg")<<"FolderColor = 99;\nScreenButtons = false;\nStatusBar = false;\n";
 string note;for(int i=0;i<60;++i)note+="Строка заметки "+to_string(i)+" — проверка прокрутки и кириллицы.\n";
 ofstream(app+"/.notes/"+wook::legacy_name(issue1)+".txt")<<note;
 check(SDL_Init(SDL_INIT_VIDEO|SDL_INIT_TIMER)==0,"SDL initialize");check(TTF_Init()==0,"TTF initialize");
 WINDOW=SDL_CreateWindow("Actual generated WookReader chooser",0,0,1280,720,SDL_WINDOW_HIDDEN);
 RENDERER=SDL_CreateRenderer(WINDOW,-1,SDL_RENDERER_SOFTWARE);check(RENDERER,"software renderer");
 ROBOTO_15=TTF_OpenFont(argv[2],15);ROBOTO_20=TTF_OpenFont(argv[2],20);ROBOTO_25=TTF_OpenFont(argv[2],25);ROBOTO_30=TTF_OpenFont(argv[2],30);ROBOTO_35=TTF_OpenFont(argv[2],35);
 check(ROBOTO_15&&ROBOTO_20&&ROBOTO_25&&ROBOTO_30&&ROBOTO_35,"actual bundled Roboto fonts");
 SDL_Surface* icon=SDL_CreateRGBSurfaceWithFormat(0,40,40,32,SDL_PIXELFORMAT_RGBA32);
 SDL_FillRect(icon,nullptr,SDL_MapRGBA(icon->format,0,170,195,255));
 SDL_Texture* host_icon=SDL_CreateTextureFromSurface(RENDERER,icon);SDL_FreeSurface(icon);
 HOST_ASSIGN_ICONS
 int title_cases=0;
 for(int cols=2;cols<=8;++cols) {
  int width=1280/cols-12;TTF_Font* font=cols<=4?ROBOTO_25:cols<=6?ROBOTO_20:ROBOTO_15;
  for(const string& title:vector<string>{"Хранители",long_title,"Рик и Морти 001","ДлинноеназваниебезпробеловДлинноеназваниебезпробелов 002","ASCII equivalent very long title of an issue 053"}) {
    const string fitted=library_fit_title(font,title,width);int measured=0;
    check(TTF_SizeUTF8(font,fitted.c_str(),&measured,nullptr)==0&&measured<=width,"UTF-8 title fits grid width");
    check(wook::casefold_utf8(fitted).find("�")==string::npos,"fitted title remains valid UTF-8");
    if(title==long_title)check(fitted.size()>=3&&fitted.substr(fitted.size()-3)=="053","issue suffix remains visible");
    SDL_Surface* rendered=TTF_RenderUTF8_Blended(font,fitted.c_str(),WHITE);check(rendered&&rendered->w>0,"Cyrillic title really renders");SDL_FreeSurface(rendered);++title_cases;
  }
 }
 verify([&](){check(snap.view.kind==LibraryViewKind::Directory,"initial real folder view");});
 press(HidNpadButton_A);
 verify([&](){check(snap.view.kind==LibraryViewKind::All&&snap.entries.size()==7,"global index includes SD /books and nested Cyrillic paths");});
 select("Хранители");menu("В избранное");
 verify([&](){check(wook::book_state(watchmen).favorite,"favorite action changes full-path state");});
 menu("Отметить прочитанным");
 verify([&](){check(wook::book_state(watchmen).finished,"finished action persists state");});
 menu("Только избранное");
 verify([&](){check(snap.entries.size()==1&&snap.entries[0].path==watchmen,"favorite filter shows matching file");});
 menu("Сбросить поиск и фильтры");menu("Только непрочитанное");
 verify([&](){check(snap.entries.size()==6,"unread filter excludes completed book");});
 menu("Сбросить поиск и фильтры");menu_keyboard("Поиск по всем комиксам","РИК МОРТИ");
 verify([&](){check(snap.entries.size()==3&&snap.entries[0].path==issue1&&snap.entries[2].path==issue10,"Cyrillic casefold search and natural issue order");});
 screenshot("search_cyrillic");
 press(HidNpadButton_Plus);press(HidNpadButton_ZL|HidNpadButton_Y|HidNpadButton_Right);
 verify([&](){check(snap.modal==LibraryModal::Options&&snap.cols==5&&opened_books.empty(),"options block underlying grid, notes and reader input");check(std::find(snap.actions.begin(),snap.actions.end(),"Цвет папок: Жёлтый")!=snap.actions.end()&&!configScreenButtons&&!configStatusBar,"partial legacy options load independently and colors are clamped");});
 press(HidNpadButton_B);menu("Сбросить поиск и фильтры");
 menu_keyboard("Создать коллекцию","Моя коллекция");pick("Рик и Морти 001");pick("Хранители");press(HidNpadButton_X);
 verify([&](){auto c=group_named("Моя коллекция");check(!c.id.empty()&&c.books.size()==2,"manual create and add selected originals");});
 menu("Коллекции и серии");select("Моя коллекция");screenshot("collections");press(HidNpadButton_A);
 select("Хранители");menu("Выпуск раньше");
 verify([&](){check(group_named("Моя коллекция").books.front()==watchmen,"manual collection order");});
 menu("Сделать обложкой коллекции");
 verify([&](){check(group_named("Моя коллекция").cover==watchmen,"explicit member cover choice");});
 menu_keyboard("Переименовать коллекцию","Моя новая коллекция");
 verify([&](){check(group_named("Моя новая коллекция").books.size()==2,"rename preserves members");});
 menu("Убрать выпуски");pick("Хранители");press(HidNpadButton_X);
 verify([&](){auto c=group_named("Моя новая коллекция");check(c.books.size()==1&&c.cover==issue1&&fs::exists(watchmen),"remove reference falls back to remaining member cover and keeps original file");});
 menu("Добавить выпуски");pick("Хранители");press(HidNpadButton_X);
 verify([&](){check(group_named("Моя новая коллекция").books.size()==2,"add member to existing collection");});
 menu("Продолжить коллекцию");
 verify([&](){check(opened_books.size()==1&&opened_books[0]==issue1&&opened_sequences[0]=="Моя новая коллекция","Continue passes ordered collection and unfinished issue to reader");});
 select("Рик и Морти 001");press(HidNpadButton_Y);
 verify([&](){check(snap.modal==LibraryModal::Notes&&snap.modal_text==note,"library loads reader-compatible legacy note");});
 press(HidNpadButton_ZL|HidNpadButton_Right);press(HidNpadButton_Down);press(HidNpadButton_R);
 verify([&](){check(snap.modal==LibraryModal::Notes&&snap.modal_scroll>0&&snap.cols==5&&opened_books.size()==1,"notes scroll while blocking all underlying grid actions");});
 screenshot("notes_scrolled");
 answer(HidNpadButton_A,"Общая заметка библиотеки и читателя");
 verify([&](){check(chooser_note_read(issue1)=="Общая заметка библиотеки и читателя","modern full-path note saved");});
 answer(HidNpadButton_A,"");
 verify([&](){check(chooser_note_read(issue1).empty()&&fs::exists(chooser_note_path(issue1)),"cleared note tombstone suppresses old fallback");});
 press(HidNpadButton_B);
 select("Хранители");
 verify([&](){char key[32];snprintf(key,sizeof(key),"%016llx",(unsigned long long)comic_cache_key(watchmen.c_str()));string p=app+"/.pagecache/"+key;fs::create_directories(app+"/.pagecache");ofstream(p+".cvr")<<"bytes";ofstream(p+".cvrn")<<"member.jpg";ofstream(p+".lst")<<"retain";});
 menu("Обновить обложку");
 verify([&](){char key[32];snprintf(key,sizeof(key),"%016llx",(unsigned long long)comic_cache_key(watchmen.c_str()));string p=app+"/.pagecache/"+key;check(!fs::exists(p+".cvr")&&!fs::exists(p+".cvrn")&&fs::exists(p+".lst"),"Retry clears only cover bytes/member name and retains page list");});
 menu("Удалить коллекцию");press(HidNpadButton_A);
 verify([&](){check(group_named("Моя новая коллекция").id.empty()&&fs::exists(watchmen)&&fs::exists(issue1),"delete collection keeps all archives");});
 select("Рик и Морти");menu("Скрыть автосерию");press(HidNpadButton_A);
 verify([&](){check(wook::automatic_collections({issue1,issue2,issue10}).empty()&&fs::exists(issue2),"hide auto series persists metadata and keeps originals");});
 menu("Все комиксы");select(long_title);screenshot("long_selected_title");
 press(HidNpadButton_Plus);menu("Экономия батареи");menu("Сохранять масштаб");menu("Сохранять положение");menu("Группировать серии");menu("Кнопки на экране");menu("Строка состояния");
 verify([&](){check(!wook::battery_saver()&&!wook::keep_zoom()&&!wook::keep_position()&&!wook::group_series(),"all four settings toggle through UI");});
 screenshot("settings");press(HidNpadButton_B);
 press(HidNpadButton_ZR);press(HidNpadButton_ZR);press(HidNpadButton_ZR);
 for(int cols=2;cols<=8;++cols){verify([cols](){check(snap.cols==cols,"grid column count");});screenshot("grid_"+to_string(cols));if(cols<8)press(HidNpadButton_ZL);}
 menu("Открыть SD-карту");
 verify([&](){check(snap.view.kind==LibraryViewKind::Directory&&snap.view.path=="/","explicit SD root navigation");});
 press(HidNpadButton_B);
 verify([&](){check(snap.view.kind==LibraryViewKind::All,"Back restores prior virtual view");});
 Menu_StartChoosing();
 check(commands.empty(),"all UI replay commands completed");
 check(keyboard_answers.empty(),"every keyboard answer consumed once");
 wook::set_storage_root_for_tests(app);wook::initialize();
 check(!wook::battery_saver()&&!wook::keep_zoom()&&!wook::keep_position()&&!wook::group_series(),"settings survive store reload");
 check(wook::book_state(watchmen).favorite&&wook::book_state(watchmen).finished,"favorite and completed survive reload");
 config_t loaded_options;config_init(&loaded_options);check(config_read_file(&loaded_options,(app+"/options.cfg").c_str()),"legacy options saved");
 int folder=0,buttons=0,status=0;config_lookup_int(&loaded_options,"FolderColor",&folder);config_lookup_bool(&loaded_options,"ScreenButtons",&buttons);config_lookup_bool(&loaded_options,"StatusBar",&status);
 check(folder==24&&buttons&&status,"clamped color and legacy display toggles persist");config_destroy(&loaded_options);
 check(errors.empty(),"no unexpected host UI errors");
 SDL_DestroyTexture(host_icon);TTF_CloseFont(ROBOTO_15);TTF_CloseFont(ROBOTO_20);TTF_CloseFont(ROBOTO_25);TTF_CloseFont(ROBOTO_30);TTF_CloseFont(ROBOTO_35);
 SDL_DestroyRenderer(RENDERER);SDL_DestroyWindow(WINDOW);TTF_Quit();SDL_Quit();
 cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"frames\":"<<frame_count<<",\"title_cases\":"<<title_cases<<",\"rendered_snapshots\":"<<screenshots.size()<<"}\n";
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("output/library-ui-tests.json"))
    parser.add_argument("--work", type=Path)
    parser.add_argument("--font", type=Path)
    parser.add_argument("--host-cflags", default="")
    parser.add_argument("--host-libs", default="")
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    chooser = args.source / "source/menus/book-chooser/MenuChooser.cpp"
    text = chooser.read_text(encoding="utf-8")
    if "WOOK_POWER_LIBRARY_DRAW:" in text:
        raise SystemExit("Run this UI replay before enhance_power.py; the independent power test covers that gate.")
    work = args.work or Path(tempfile.mkdtemp(prefix="wook-library-ui-"))
    work.mkdir(parents=True, exist_ok=True)
    runtime = (work / "runtime").resolve()
    # All writes are confined to this test's private directory.
    if runtime.exists():
        import shutil
        shutil.rmtree(runtime)
    runtime.mkdir()
    adapted = text.replace('"/switch/WookReader', json.dumps(str(runtime))[0:-1] + '/switch/WookReader')
    adapted = adapted.replace('"/books"', json.dumps(str(runtime / "books")))
    a = adapted.index("static const char* CHOOSER_NOTES_DIR")
    b = adapted.index("// ── Grid layout", a)
    notes_and_recent = adapted[a:b]
    cache = adapted[adapted.index("struct LRUTextureCache {"):adapted.index("template <typename T>")]
    loader = adapted[adapted.index("struct CoverLoader {"):adapted.index("// ── Main chooser")]
    helpers = adapted[adapted.index("// BEGIN WOOK_LIBRARY_HELPERS"):adapted.index("// END WOOK_LIBRARY_HELPERS")]
    body = function(adapted, "void Menu_StartChoosing()")
    # Observes real local state and captures the actual framebuffer after drawing.
    body = body.replace("    SDL_RenderPresent(RENDERER);", "    observe(view, entries, modal, actions, picker_paths, chosen_index, modal_index, modal_scroll, cols, modal_text, cover_failed);\n    SDL_RenderPresent(RENDERER);", 1)
    import re
    textures = sorted(set(re.findall(r"\b[a-z_]+_(?:folder|book)\b", body)))
    textures = [t for t in textures if t not in {"open_book", "continue_book", "add_book", "library_is_book", "clear_reading_sequence", "next_book"} and (t + "," in body or t + "}" in body)]
    declared = "\n".join("static SDL_Texture* " + texture + "=nullptr;" for texture in textures)
    main_cpp = MAIN.replace("HOST_ASSIGN_ICONS", "\n".join(texture + "=host_icon;" for texture in textures))
    code = PRELUDE + "\n" + declared + "\n" + notes_and_recent + "\nstatic const int FOLDER_H=40;\n" + cache
    code += function(adapted, "static int load_config(") + "\n" + function(adapted, "static void save_config(")
    code += '\nstatic const char PAGECACHE_DIR[] = ' + json.dumps(str(runtime / "switch/WookReader/.pagecache")) + ';\n'
    code += function(adapted, "static uint64_t comic_cache_key(") + "\n" + loader + "\n" + helpers + "\n" + DRIVER + "\n" + body + "\n" + main_cpp
    source = work / "actual_library_host.cpp"
    source.write_text(code, encoding="utf-8")
    font = args.font or args.source / "romfs/resources/font/Roboto-Light.ttf"
    if args.host_cflags or args.host_libs:
        flags = shlex.split(args.host_cflags)
        libs = shlex.split(args.host_libs)
    else:
        flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "sdl2", "SDL2_ttf", "libconfig"], text=True))
        libs = shlex.split(subprocess.check_output(["pkg-config", "--libs", "sdl2", "SDL2_ttf", "libconfig"], text=True))
    binary = work / "actual-library-host"
    command = ["g++", "-std=c++17", "-O1", "-g", "-fno-exceptions", "-fno-rtti", "-pthread", "-I" + str(package), *flags, str(source), str(package / "WookLibrary.cpp"), *libs, "-o", str(binary)]
    build = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (work / "compile.log").write_text(build.stdout)
    if build.returncode:
        raise SystemExit(build.stdout)
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    run = subprocess.run([str(binary), str(runtime), str(font), str(work / "screenshots")], env=env, capture_output=True, text=True)
    (work / "run.log").write_text(run.stdout + run.stderr)
    if run.returncode:
        raise SystemExit(run.stdout + run.stderr)
    result = json.loads(run.stdout)
    result.update(scope="Actual generated library UI replay and SDL software rendering; host service and synthetic cover/reader adapters; no physical Switch execution",
                  chooser_sha256=hashlib.sha256(chooser.read_bytes()).hexdigest(),
                  test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  state_sha256=hashlib.sha256((package / "WookLibrary.cpp").read_bytes()).hexdigest(),
                  font_sha256=hashlib.sha256(font.read_bytes()).hexdigest(),
                  command=command, screenshots=[str(p) for p in sorted((work / "screenshots").glob("*.bmp"))])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
