/* Process-local adapter for Filament 1.9.9's malformed WGL flags pair.
 * This source changes no Windows driver or existing application's state.
 * All OpenGL exports other than the proc-address lookup are direct forwards
 * to the verified Mesa loader. The one correction is exact and counted.
 */
#include "win32_minimal.h"

typedef HGLRC (WINAPI *CreateContextProc)(HDC,HGLRC,const int *);
typedef PROC (WINAPI *GetProcProc)(LPCSTR);
typedef BOOL (WINAPI *PixelAttributesProc)(HDC,int,int,unsigned,const int*,int*);
static HINSTANCE self_module;
static HMODULE mesa_module;
static INIT_ONCE initialized=INIT_ONCE_STATIC_INIT;
static GetProcProc mesa_get_proc;
static CreateContextProc mesa_create_context;
static PixelAttributesProc mesa_pixel_attributes;
static volatile LONG fixes=0;
static volatile LONG framebuffer_filters=0;

int WINAPI LabWglApplyFramebufferPolicy(int format,int target,unsigned count,const int *attributes,int *values) {
    unsigned i; int draw_index=-1,support_index=-1;
    if(!attributes || !values || target<=0) return 0;
    for(i=0;i<count;i++) {
        if(attributes[i]==0x2001) draw_index=(int)i;
        if(attributes[i]==0x2010) support_index=(int)i;
    }
    if(draw_index>=0 && support_index>=0 && format!=target && values[draw_index]) {
        values[draw_index]=0;
        return 1;
    }
    return 0;
}

static BOOL WINAPI pixel_attributes(HDC dc,int format,int layer,unsigned count,const int *attributes,int *values) {
    BOOL result; int target;
    /* Exactly the pixel descriptor used by Filament 1.9.9 PlatformWGL. */
    static const PIXELFORMATDESCRIPTOR filament_format={
        sizeof(PIXELFORMATDESCRIPTOR),1,0x00000004|0x00000020|0x00000001,
        0,32,0,0,0,0,0,0,0,0,0,0,0,0,0,24,0,0,0,0,0,0,0
    };
    if(!mesa_pixel_attributes) return FALSE;
    result=mesa_pixel_attributes(dc,format,layer,count,attributes,values);
    if(!result || layer!=0) return result;
    target=ChoosePixelFormat(dc,&filament_format);
    if(LabWglApplyFramebufferPolicy(format,target,count,attributes,values))
        _InterlockedIncrement(&framebuffer_filters);
    return result;
}

static int same_string(const char *a,const char *b) {
    if(!a || !b) return 0;
    while(*a && *a==*b) { ++a; ++b; }
    return *a==*b;
}

int WINAPI LabWglNormalizeAttributes(const int *input,int *output,unsigned capacity) {
    unsigned i=0;
    if(!output || !capacity) return 0;
    if(!input) { output[0]=0; return 1; }
    while(i<capacity && input[i]) {
        if(i+2>=capacity) return 0;
        output[i]=input[i]; output[i+1]=input[i+1];
        if(input[i]==0x2094 && input[i+1]==0x9126) output[i+1]=0;
        i+=2;
    }
    if(i>=capacity) return 0;
    output[i]=0;
    return (int)i+1;
}

static BOOL CALLBACK initialize(PINIT_ONCE once,PVOID param,PVOID *context) {
    WCHAR *path; DWORD length; unsigned i;
    const WCHAR filename[]=L"opengl32_mesa.dll";
    (void)once; (void)param; (void)context;
    path=(WCHAR*)HeapAlloc(GetProcessHeap(),0,32768*sizeof(WCHAR));
    if(!path) return FALSE;
    length=GetModuleFileNameW(self_module,path,32768);
    if (!length || length>=32768) { HeapFree(GetProcessHeap(),0,path); return FALSE; }
    while(length && path[length-1]!=L'\\') length--;
    if(length+18>=32768) { HeapFree(GetProcessHeap(),0,path); return FALSE; }
    for(i=0;i<18;i++) { path[length+i]=filename[i]; if(!filename[i]) break; }
    mesa_module=LoadLibraryExW(path,NULL,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    HeapFree(GetProcessHeap(),0,path);
    if(!mesa_module) return FALSE;
    mesa_get_proc=(GetProcProc)GetProcAddress(mesa_module,"wglGetProcAddress");
    return mesa_get_proc!=NULL;
}

static HGLRC WINAPI create_context(HDC dc,HGLRC shared,const int *attributes) {
    int corrected[256]; int i=0; int count;
    if(!mesa_create_context) { SetLastError(ERROR_PROC_NOT_FOUND); return NULL; }
    if(!attributes) return mesa_create_context(dc,shared,NULL);
    count=LabWglNormalizeAttributes(attributes,corrected,256);
    if(!count) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    while(i<count-1) {
        /* Filament 1.9.9: FLAGS_ARB (0x2094) mistakenly receives the
         * PROFILE_MASK_ARB enum (0x9126). No actual flags were intended. */
        if(attributes[i]==0x2094 && attributes[i+1]==0x9126) {
            _InterlockedIncrement(&fixes);
        }
        i+=2;
    }
    return mesa_create_context(dc,shared,corrected);
}

PROC WINAPI wglGetProcAddress(LPCSTR name) {
    if(!InitOnceExecuteOnce(&initialized,initialize,NULL,NULL)) return NULL;
    if(same_string(name,"wglCreateContextAttribsARB")) {
        mesa_create_context=(CreateContextProc)mesa_get_proc(name);
        return mesa_create_context ? (PROC)create_context : NULL;
    }
    if(same_string(name,"wglGetPixelFormatAttribivARB")) {
        mesa_pixel_attributes=(PixelAttributesProc)mesa_get_proc(name);
        return mesa_pixel_attributes ? (PROC)pixel_attributes : NULL;
    }
    return mesa_get_proc(name);
}

LONG WINAPI LabWglCompatibilityFixCount(void) { return _InterlockedCompareExchange(&fixes,0,0); }
LONG WINAPI LabWglFramebufferFilterCount(void) { return _InterlockedCompareExchange(&framebuffer_filters,0,0); }
int WINAPI LabWglPixelFormatDescriptorSize(void) { return sizeof(PIXELFORMATDESCRIPTOR); }

BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID reserved) {
    (void)reserved;
    if(reason==DLL_PROCESS_ATTACH) { self_module=module; DisableThreadLibraryCalls(module); }
    return TRUE;
}
