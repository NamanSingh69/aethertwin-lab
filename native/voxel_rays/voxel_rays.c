/* Exact Amanatides-Woo traversal. No allocation, CRT, fast-math or vendor SDK.
 * ABI: int64 coordinates/offsets/owners, IEEE-754 double observations.
 * Both ends included. Simultaneous axes use the reference absolute 1e-12 tie.
 */
#ifdef _WIN32
#define API __declspec(dllexport)
int _fltused=0;
#else
#define API __attribute__((visibility("default")))
#endif
typedef long long i64;
#ifdef __cplusplus
extern "C" {
#endif
#define HUGE_DOUBLE 1.7976931348623157e308
#define INDEX_LIMIT 9007199254740991.0
static double absolute(double value){return value<0?-value:value;}
static int finite_number(double value){return value<=HUGE_DOUBLE&&value>=-HUGE_DOUBLE;}
static i64 floor_index(double value){i64 result=(i64)value;return (double)result>value?result-1:result;}

API int lab_voxel_ray_api_version(void){return 1;}

static int compare_key(const i64* a,const i64* b){
    int axis;for(axis=0;axis<3;axis++){if(a[axis]<b[axis])return -1;if(a[axis]>b[axis])return 1;}return 0;
}
static void swap_key(i64* a,i64* b){
    int axis;for(axis=0;axis<3;axis++){i64 temporary=a[axis];a[axis]=b[axis];b[axis]=temporary;}
}
static void heap_down(i64* keys,i64 root,i64 end){
    for(;;){
        i64 child=2*root+1;
        if(child>=end)return;
        if(child+1<end&&compare_key(keys+3*child,keys+3*(child+1))<0)child++;
        if(compare_key(keys+3*root,keys+3*child)>=0)return;
        swap_key(keys+3*root,keys+3*child);root=child;
    }
}

/* Exact signed lexicographic unique, in-place O(N log N), O(1) workspace.
 * Kept separate so ordered per-ray sequences and their owners remain intact.
 */
API int lab_voxel_unique(i64* keys,i64 count,i64* unique_count){
    i64 index,kept;
    if(count<0||!unique_count||(!keys&&count))return 1;
    if(!count){*unique_count=0;return 0;}
    for(index=count/2;index>0;index--)heap_down(keys,index-1,count);
    for(index=count-1;index>0;index--){swap_key(keys,keys+3*index);heap_down(keys,0,index);}
    kept=1;
    for(index=1;index<count;index++){
        if(compare_key(keys+3*(kept-1),keys+3*index)){
            int axis;for(axis=0;axis<3;axis++)keys[3*kept+axis]=keys[3*index+axis];kept++;
        }
    }
    *unique_count=kept;return 0;
}

/* First call with keys==NULL computes offsets, second call fills exact rows.
 * Return: 0 success, 1 invalid data, 2 count/index overflow, 3 capacity limit,
 * 4 impossible endpoint (a defensive invariant, never a partial valid result).
 */
API int lab_voxel_trace(const double* origins,const double* targets,i64 ray_count,double size,
                       i64* offsets,i64* keys,i64* owners,i64 capacity){
    i64 ray,total=0;
    if(!finite_number(size)||size<=0||ray_count<0||capacity<0||!offsets)return 1;
    offsets[0]=0;
    for(ray=0;ray<ray_count;ray++){
        double o[3],t[3],direction[3],delta[3],maximum[3];
        i64 cell[3],end[3],step[3],axis,limit=3,iteration=0;
        for(axis=0;axis<3;axis++){
            o[axis]=origins[3*ray+axis]/size;t[axis]=targets[3*ray+axis]/size;
            if(!finite_number(o[axis])||!finite_number(t[axis])||absolute(o[axis])>INDEX_LIMIT||absolute(t[axis])>INDEX_LIMIT)return 1;
            cell[axis]=floor_index(o[axis]);end[axis]=floor_index(t[axis]);direction[axis]=t[axis]-o[axis];
            step[axis]=(direction[axis]>0)-(direction[axis]<0);
            delta[axis]=step[axis]?1.0/absolute(direction[axis]):HUGE_DOUBLE;
            maximum[axis]=step[axis]?((double)(cell[axis]+(step[axis]>0))-o[axis])/direction[axis]:HUGE_DOUBLE;
            if(cell[axis]==end[axis])maximum[axis]=HUGE_DOUBLE;
            {i64 distance=cell[axis]>end[axis]?cell[axis]-end[axis]:end[axis]-cell[axis];limit+=distance;}
        }
        for(;;){
            double minimum;
            if(total>=capacity)return 3;
            if(keys){for(axis=0;axis<3;axis++)keys[3*total+axis]=cell[axis];owners[total]=ray;}
            total++;
            if(cell[0]==end[0]&&cell[1]==end[1]&&cell[2]==end[2])break;
            if(iteration++>=limit)return 4;
            minimum=maximum[0];if(maximum[1]<minimum)minimum=maximum[1];if(maximum[2]<minimum)minimum=maximum[2];
            for(axis=0;axis<3;axis++){
                if(absolute(maximum[axis]-minimum)<=1e-12){cell[axis]+=step[axis];maximum[axis]+=delta[axis];}
                if(cell[axis]==end[axis])maximum[axis]=HUGE_DOUBLE;
            }
        }
        offsets[ray+1]=total;
    }
    return 0;
}
#ifdef __cplusplus
}
#endif
