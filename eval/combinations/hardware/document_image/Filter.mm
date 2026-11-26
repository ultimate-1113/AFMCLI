#import <Accelerate/Accelerate.h>
#import <CommonCrypto/CommonDigest.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShaders/MetalPerformanceShaders.h>
#include <mach/mach_time.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

using Image = std::vector<uint8_t>;
constexpr int widthOfKernel = 5, radius = widthOfKernel / 2;
static uint64_t now() {
    static const auto ratio = [] { mach_timebase_info_data_t t; mach_timebase_info(&t); return t; }();
    return uint64_t((__uint128_t)mach_absolute_time() * ratio.numer / ratio.denom);
}
static void require(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
static NSString *digest(const Image &pixels) {
    uint8_t hash[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(pixels.data(), static_cast<CC_LONG>(pixels.size()), hash);
    char hex[65];
    for (int i=0; i<32; ++i) std::snprintf(hex+2*i, 3, "%02x", hash[i]);
    return [NSString stringWithUTF8String:hex];
}
static void emit(NSDictionary *record) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:record options:NSJSONWritingSortedKeys error:nil];
    require(json != nil, "JSON encoding failed");
    std::fwrite(json.bytes, 1, json.length, stdout);
    std::putchar('\n'); std::fflush(stdout);
}

static uint32_t randomPixel(uint32_t i) {
    uint32_t v = i + 0x84512ab3u;
    v ^= v >> 16; v *= 0x7feb352du; v ^= v >> 15; v *= 0x846ca68bu;
    return v ^ (v >> 16);
}

static Image form(int w, int h) {
    // Header number 42, followed by a ruled table with short printed entries.
    const char *glyph[2][7] = {{"10010","10010","10010","11111","00010","00010","00010"},
                                {"01110","10001","00001","00010","00100","01000","11111"}};
    const int scale = std::max(7,w/30), left = (w-11*scale)/2, top = h/18;
    const int margin=w/16, table=h*37/100, row=std::max(28,h/22), col=(w-2*margin)/3;
    Image page(size_t(w)*h);
    for (int y=0; y<h; ++y) for (int x=0; x<w; ++x) {
        uint8_t pixel = uint8_t(239 + (uint32_t(x)*7/w) + (uint32_t(y)*6/h));
        const int hx = x-left, hy=y-top;
        if (hx>=0 && hy>=0 && hy<7*scale && hx<11*scale) {
            const int digit=hx/(6*scale), column=(hx/scale)%6;
            if (digit<2 && column<5 && glyph[digit][hy/scale][column]=='1') pixel=24;
        }
        if (x>=margin && x<=w-margin && y>=table && y<h-margin) {
            const int rx=x-margin, ry=y-table, cx=rx%col, cy=ry%row, idx=ry/row;
            if (cy<6 || cx<6) pixel=96;
            else if (cy>row/3 && cy<row/3+std::max(7,row/6) && cx>col/10 &&
                     cx<col*(3+(idx+rx/col)%3)/8 && (cx/(std::max(7,col/28)))%4 != 3)
                pixel=uint8_t(40+idx%30);
        }
        page[size_t(y)*w+x]=pixel;
    }
    return page;
}

static void referencePass(const Image &src, Image &dst, Image &tmp, int w, int h, bool maximum) {
    auto choose = [maximum](uint8_t a,uint8_t b) { return maximum ? std::max(a,b) : std::min(a,b); };
    for (int y=0; y<h; ++y) for (int x=0; x<w; ++x) {
        auto v=src[size_t(y)*w+x];
        for (int dx=-radius; dx<=radius; ++dx) v=choose(v,src[size_t(y)*w+std::clamp(x+dx,0,w-1)]);
        tmp[size_t(y)*w+x]=v;
    }
    for (int y=0; y<h; ++y) for (int x=0; x<w; ++x) {
        auto v=tmp[size_t(y)*w+x];
        for (int dy=-radius; dy<=radius; ++dy) v=choose(v,tmp[size_t(std::clamp(y+dy,0,h-1))*w+x]);
        dst[size_t(y)*w+x]=v;
    }
}

struct CPUWorkspace {
    Image tmp;
    explicit CPUWorkspace(size_t n): tmp(n) {}
    void process(Image &src, Image &dst, int w, int h) {
        auto view = [&](Image &v) { return vImage_Buffer{v.data(),(vImagePixelCount)h,(vImagePixelCount)w,(size_t)w}; };
        vImage_Buffer input=view(src), middle=view(tmp), output=view(dst);
        auto pass = [&](bool maximum,vImage_Buffer &a,vImage_Buffer &b) {
            auto fn=maximum ? vImageMax_Planar8 : vImageMin_Planar8;
            require(fn(&a,&b,nullptr,0,0,widthOfKernel,widthOfKernel,kvImageNoFlags)==kvImageNoError,"vImage failed");
        };
        pass(false,input,middle); pass(true,middle,output); pass(true,output,middle); pass(false,middle,output);
    }
};

struct GPUContext {
    id<MTLDevice> device;
    id<MTLCommandQueue> queue;
    MPSImageAreaMin *minimum;
    MPSImageAreaMax *maximum;
    GPUContext() {
        device=MTLCreateSystemDefaultDevice();
        require(device && MPSSupportsMTLDevice(device),"Metal MPS device unavailable");
        queue=[device newCommandQueue];
        minimum=[[MPSImageAreaMin alloc] initWithDevice:device kernelWidth:widthOfKernel kernelHeight:widthOfKernel];
        maximum=[[MPSImageAreaMax alloc] initWithDevice:device kernelWidth:widthOfKernel kernelHeight:widthOfKernel];
        minimum.edgeMode=MPSImageEdgeModeClamp; maximum.edgeMode=MPSImageEdgeModeClamp;
        require(queue && minimum && maximum,"MPS setup failed");
    }
};
struct GPUWorkspace {
    id<MTLBuffer> upload, download;
    id<MTLTexture> a,b;
    GPUWorkspace(GPUContext &g,int w,int h) {
        const size_t n=size_t(w)*h;
        upload=[g.device newBufferWithLength:n options:MTLResourceStorageModeShared];
        download=[g.device newBufferWithLength:n options:MTLResourceStorageModeShared];
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm
                                        width:w height:h mipmapped:NO];
        d.storageMode=MTLStorageModePrivate; d.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;
        a=[g.device newTextureWithDescriptor:d]; b=[g.device newTextureWithDescriptor:d];
        require(a && b && upload && download,"Metal workspace allocation failed");
    }
    void process(GPUContext &g,const Image &src,Image &dst,int w,int h,uint64_t &submitted,
                 uint64_t &gpuStart,uint64_t &gpuEnd) {
        std::memcpy(upload.contents,src.data(),src.size());
        id<MTLCommandBuffer> cmd=[g.queue commandBuffer];
        const MTLSize size=MTLSizeMake(w,h,1); const MTLOrigin origin=MTLOriginMake(0,0,0);
        id<MTLBlitCommandEncoder> in=[cmd blitCommandEncoder];
        [in copyFromBuffer:upload sourceOffset:0 sourceBytesPerRow:w sourceBytesPerImage:src.size()
               sourceSize:size toTexture:a destinationSlice:0 destinationLevel:0 destinationOrigin:origin];
        [in endEncoding];
        [g.minimum encodeToCommandBuffer:cmd sourceTexture:a destinationTexture:b];
        [g.maximum encodeToCommandBuffer:cmd sourceTexture:b destinationTexture:a];
        [g.maximum encodeToCommandBuffer:cmd sourceTexture:a destinationTexture:b];
        [g.minimum encodeToCommandBuffer:cmd sourceTexture:b destinationTexture:a];
        id<MTLBlitCommandEncoder> out=[cmd blitCommandEncoder];
        [out copyFromTexture:a sourceSlice:0 sourceLevel:0 sourceOrigin:origin sourceSize:size toBuffer:download
            destinationOffset:0 destinationBytesPerRow:w destinationBytesPerImage:dst.size()];
        [out endEncoding]; submitted=now();
        [cmd commit]; [cmd waitUntilCompleted];
        require(cmd.status==MTLCommandBufferStatusCompleted && !cmd.error,"Metal command failed");
        gpuStart=uint64_t(std::llround(cmd.GPUStartTime*1e9));
        gpuEnd=uint64_t(std::llround(cmd.GPUEndTime*1e9));
        require(gpuStart>0 && gpuEnd>gpuStart,"Metal hardware clock missing");
        std::memcpy(dst.data(),download.contents,dst.size());
    }
};

static void serve(int w,int h) {
    require((w==512 && h==768) || (w==2048 && h==3072) || (w==4096 && h==6144),"shape outside protocol");
    const size_t n=size_t(w)*h;
    uint64_t begin=now();
    Image clean=form(w,h), source=clean, dst(n), reference(n), tmp(n), between(n);
    uint64_t badPixels=0, initialError=0, finalError=0, changed=0;
    for (size_t i=0; i<n; ++i) {
        uint32_t r=randomPixel(uint32_t(i));
        if ((r&127)==0) source[i]=(r&128)?255:0;
        badPixels+=source[i]!=clean[i];
        initialError+=std::abs(int(source[i])-clean[i]);
    }
    uint64_t generated=now();
    referencePass(source,between,tmp,w,h,false); referencePass(between,reference,tmp,w,h,true);
    referencePass(reference,between,tmp,w,h,true); referencePass(between,reference,tmp,w,h,false);
    for (size_t i=0;i<n;++i) {
        finalError+=std::abs(int(reference[i])-clean[i]);
        changed+=source[i]!=reference[i];
    }
    uint64_t referenced=now();
    Image().swap(tmp); Image().swap(between); Image().swap(clean);
    GPUContext gpu;
    uint64_t contextReady=now();
    CPUWorkspace cpuPool(n); uint64_t cpuPoolReady=now();
    GPUWorkspace gpuPool(gpu,w,h); uint64_t gpuPoolReady=now();
    NSString *expected=digest(reference);
    const int scale=std::max(7,w/30), left=(w-11*scale)/2, top=h/18, margin=w/16, table=h*37/100;
    NSMutableArray *samples=[NSMutableArray array];
    const std::pair<int,int> points[]={{0,0},{w-1,0},{0,h-1},{w-1,h-1},{1,h/2},{w/2,1},
        {left+scale/2,top+scale/2},{left+scale,top+scale/2},{left+8*scale,top+1},
        {margin+4,table+4},{margin+7,table+7},{w/3,h/2},{w/2,h/2},{w-2,h/3}};
    for (auto [x,y] : points) [samples addObject:@[@(x),@(y),@(reference[size_t(y)*w+x])]];
    emit(@{@"event":@"ready",@"width":@(w),@"height":@(h),@"kernel":@(widthOfKernel),@"device":gpu.device.name,
           @"source_sha256":digest(source),@"reference_sha256":expected,@"noise_pixels":@(badPixels),
           @"reference_samples":samples,
           @"input_abs_error":@(initialError),@"output_abs_error":@(finalError),@"changed_pixels":@(changed),
           @"generation_ns":@(generated-begin),@"reference_ns":@(referenced-generated),
           @"context_ns":@(contextReady-referenced),@"cpu_pool_ns":@(cpuPoolReady-contextReady),
           @"gpu_pool_ns":@(gpuPoolReady-cpuPoolReady)});
    std::string line;
    while (std::getline(std::cin,line)) @autoreleasepool {
        NSData *data=[NSData dataWithBytes:line.data() length:line.size()];
        NSDictionary *q=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        NSString *backend=q[@"backend"];
        bool useGPU=[backend isEqual:@"mps"], pooled=[q[@"pooled"] boolValue];
        int repeats=[q[@"repeats"] intValue];
        require((useGPU || [backend isEqual:@"vimage"]) && repeats>=1 && repeats<=64,"invalid job");
        NSMutableArray *frames=[NSMutableArray array];
        for (int i=0;i<repeats;++i) @autoreleasepool {
            uint64_t start=now(), issued=0, gpuStart=0, gpuEnd=0;
            std::unique_ptr<GPUWorkspace> freshGPU;
            std::unique_ptr<CPUWorkspace> freshCPU;
            if (!pooled && useGPU) freshGPU=std::make_unique<GPUWorkspace>(gpu,w,h);
            if (!pooled && !useGPU) freshCPU=std::make_unique<CPUWorkspace>(n);
            uint64_t prepared=now();
            if (useGPU) (freshGPU ? *freshGPU:gpuPool).process(gpu,source,dst,w,h,issued,gpuStart,gpuEnd);
            else (freshCPU ? *freshCPU:cpuPool).process(source,dst,w,h);
            uint64_t delivered=now();
            require(std::memcmp(dst.data(),reference.data(),n)==0,"absolute pixel mismatch");
            [frames addObject:@{@"start":@(start),@"prepared":@(prepared),@"submitted":@(issued),
                                @"gpu_start":@(gpuStart),@"gpu_end":@(gpuEnd),@"delivered":@(delivered),@"checked":@(now())}];
        }
        if (NSString *path=q[@"dump"]) {
            std::ofstream f(path.fileSystemRepresentation,std::ios::binary);
            f.write(reinterpret_cast<const char *>(dst.data()),dst.size());
            require(bool(f),"cannot write temporary image");
        }
        if (NSString *path=q[@"dump_source"]) {
            std::ofstream f(path.fileSystemRepresentation,std::ios::binary);
            f.write(reinterpret_cast<const char *>(source.data()),source.size());
            require(bool(f),"cannot write temporary source");
        }
        emit(@{@"frames":frames,@"all_pixels_equal":@YES,@"output_sha256":digest(dst),@"backend":backend,
               @"pooled":@(pooled),@"pixels_compared":@(n*repeats)});
    }
}
int main(int argc,char **argv) {
    @autoreleasepool {
        try {
            require(argc==3,"usage: filter WIDTH HEIGHT");
            serve(std::stoi(argv[1]),std::stoi(argv[2]));
        } catch (const std::exception &e) { std::cerr<<e.what()<<'\n'; return 1; }
    }
    return 0;
}
