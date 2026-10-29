/* Minimal public Win32 ABI declarations for a no-CRT, x64 DLL. This avoids
 * requiring a separately installed Windows SDK for the compatibility adapter.
 * Function signatures: learn.microsoft.com/windows/win32/api/libloaderapi/,
 * /synchapi/, /heapapi/, /errhandlingapi/. Not a driver or UI automation API.
 */
#if !defined(_WIN64)
#error This adapter is supported only for x64 Windows.
#endif
#define WINAPI __stdcall
#define CALLBACK __stdcall
#define NULL ((void*)0)
#define TRUE 1
#define FALSE 0
typedef int BOOL;
typedef unsigned long DWORD;
typedef long LONG;
typedef unsigned short WCHAR;
typedef unsigned short WORD;
typedef unsigned char BYTE;
typedef void *PVOID;
typedef void *LPVOID;
typedef void *HINSTANCE;
typedef void *HMODULE;
typedef void *HDC;
typedef void *HGLRC;
typedef const char *LPCSTR;
typedef __int64 INT_PTR;
typedef INT_PTR (WINAPI *PROC)(void);
typedef struct { void *Ptr; } INIT_ONCE,*PINIT_ONCE;
typedef BOOL (CALLBACK *PINIT_ONCE_FN)(PINIT_ONCE,PVOID,PVOID*);
#define INIT_ONCE_STATIC_INIT {0}
#define LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR 0x00000100
#define LOAD_LIBRARY_SEARCH_DEFAULT_DIRS 0x00001000
#define DLL_PROCESS_ATTACH 1
#define ERROR_PROC_NOT_FOUND 127
#define ERROR_INVALID_PARAMETER 87
__declspec(dllimport) DWORD WINAPI GetModuleFileNameW(HMODULE,WCHAR*,DWORD);
__declspec(dllimport) HMODULE WINAPI LoadLibraryExW(const WCHAR*,void*,DWORD);
__declspec(dllimport) PROC WINAPI GetProcAddress(HMODULE,LPCSTR);
__declspec(dllimport) BOOL WINAPI InitOnceExecuteOnce(PINIT_ONCE,PINIT_ONCE_FN,PVOID,PVOID*);
__declspec(dllimport) BOOL WINAPI DisableThreadLibraryCalls(HMODULE);
__declspec(dllimport) void WINAPI SetLastError(DWORD);
__declspec(dllimport) void* WINAPI GetProcessHeap(void);
__declspec(dllimport) void* WINAPI HeapAlloc(void*,DWORD,unsigned __int64);
__declspec(dllimport) BOOL WINAPI HeapFree(void*,DWORD,void*);
typedef struct {
    WORD nSize,nVersion; DWORD dwFlags;
    BYTE iPixelType,cColorBits,cRedBits,cRedShift,cGreenBits,cGreenShift,cBlueBits,cBlueShift;
    BYTE cAlphaBits,cAlphaShift,cAccumBits,cAccumRedBits,cAccumGreenBits,cAccumBlueBits,cAccumAlphaBits;
    BYTE cDepthBits,cStencilBits,cAuxBuffers,iLayerType,bReserved;
    DWORD dwLayerMask,dwVisibleMask,dwDamageMask;
} PIXELFORMATDESCRIPTOR;
__declspec(dllimport) int WINAPI ChoosePixelFormat(HDC,const PIXELFORMATDESCRIPTOR*);
LONG _InterlockedIncrement(LONG volatile*);
LONG _InterlockedCompareExchange(LONG volatile*,LONG,LONG);
#pragma intrinsic(_InterlockedIncrement)
#pragma intrinsic(_InterlockedCompareExchange)
