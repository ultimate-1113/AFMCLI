#import <Accelerate/Accelerate.h>
#import <CommonCrypto/CommonDigest.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShaders/MetalPerformanceShaders.h>
#import <mach/mach_time.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using std::vector;
static constexpr int K = 1024, BankRows = 1024;
static void need(bool ok, const char *msg) { if (!ok) throw std::runtime_error(msg); }
static uint64_t now() {
    static mach_timebase_info_data_t tb = [] { mach_timebase_info_data_t t; mach_timebase_info(&t); return t; }();
    return (uint64_t)((__uint128_t)mach_absolute_time() * tb.numer / tb.denom);
}
static NSString *hashBytes(const void *data, size_t bytes) {
    unsigned char result[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data, (CC_LONG)bytes, result);
    NSMutableString *hex = [NSMutableString string];
    for (auto x : result) [hex appendFormat:@"%02x", x];
    return hex;
}
static void emit(id obj) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:obj options:0 error:nil];
    need(data != nil, "JSON serialization");
    fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout); fflush(stdout);
}
static vector<uint64_t> packBits(const float *x, int n) {
    vector<uint64_t> packed(size_t(n) * (K / 64));
    for (size_t i = 0; i < size_t(n) * K; ++i) {
        need(x[i] == 0 || x[i] == 1, "nonbinary source");
        if (x[i] == 1) packed[i / 64] |= uint64_t(1) << (i % 64);
    }
    return packed;
}
static int dot(const vector<uint64_t> &docs, const vector<uint64_t> &qs, int row, int col) {
    int sum = 0;
    for (int i = 0; i < K / 64; ++i)
        sum += __builtin_popcountll(docs[size_t(row) * (K/64) + i] & qs[size_t(col) * (K/64) + i]);
    return sum;
}
struct Tick {
    uint64_t start, packed, submitted, completed, materialized, checked, gpuStart = 0, gpuEnd = 0;
};
class Batch {
public:
    int m, q;
    id<MTLDevice> dev;
    id<MTLCommandQueue> queue;
    id<MTLBuffer> a, t[2], stream, c;
    MPSMatrix *left, *right[2], *changing, *out;
    MPSMatrixMultiplication *kernel;
    NSData *queryFile;
    size_t cStride, outBytes;
    vector<float> cpu, host, reference[2];
    vector<uint64_t> docBits, queryBits[2];
    uint64_t readNs, setupNs;
    Batch(int rows, int qs, NSString *dir) : m(rows), q(qs) {
        need(0 < m && m <= 4096 && 0 < q && q <= BankRows, "shape");
        uint64_t t0 = now();
        dev = MTLCreateSystemDefaultDevice();
        need(dev != nil && MPSSupportsMTLDevice(dev), "no Apple Metal/MPS device visible");
        queue = [dev newCommandQueue]; need(queue != nil, "Metal command queue");
        size_t ab = size_t(m)*K*4, qb = size_t(q)*K*4;
        outBytes = size_t(m)*q*4;
        cStride = [MPSMatrixDescriptor rowBytesFromColumns:q dataType:MPSDataTypeFloat32];
        size_t estimate = 2*ab + 6*qb + size_t(m)*cStride + 5*outBytes + 32*1024*1024;
        need(estimate < 512u*1024*1024 && (!dev.recommendedMaxWorkingSetSize || estimate < dev.recommendedMaxWorkingSetSize/2),
             "memory guard exceeded");
        NSString *da = [dir stringByAppendingPathComponent:@"documents.f32"];
        NSString *qt = [dir stringByAppendingPathComponent:@"queries.f32"];
        NSData *docs = [NSData dataWithContentsOfFile:da options:NSDataReadingMappedIfSafe error:nil];
        queryFile = [NSData dataWithContentsOfFile:qt options:NSDataReadingMappedIfSafe error:nil];
        need(docs.length == size_t(4096)*K*4 && queryFile.length == size_t(2)*BankRows*K*4, "fixture byte length");
        auto t1 = now();
        a = [dev newBufferWithBytes:docs.bytes length:ab options:MTLResourceStorageModeShared];
        auto desc = [](int rows, int cols, size_t stride) {
            return [MPSMatrixDescriptor matrixDescriptorWithRows:rows columns:cols rowBytes:stride dataType:MPSDataTypeFloat32];
        };
        left = [[MPSMatrix alloc] initWithBuffer:a descriptor:desc(m,K,K*4)];
        for (int bank=0; bank<2; ++bank) {
            t[bank] = [dev newBufferWithBytes:querySource(bank) length:qb options:MTLResourceStorageModeShared];
            right[bank] = [[MPSMatrix alloc] initWithBuffer:t[bank] descriptor:desc(q,K,K*4)];
            queryBits[bank] = packBits(querySource(bank),q);
        }
        stream = [dev newBufferWithLength:qb options:MTLResourceStorageModeShared];
        changing = [[MPSMatrix alloc] initWithBuffer:stream descriptor:desc(q,K,K*4)];
        c = [dev newBufferWithLength:size_t(m)*cStride options:MTLResourceStorageModeShared];
        out = [[MPSMatrix alloc] initWithBuffer:c descriptor:desc(m,q,cStride)];
        kernel = [[MPSMatrixMultiplication alloc] initWithDevice:dev transposeLeft:NO transposeRight:YES
                    resultRows:m resultColumns:q interiorColumns:K alpha:1 beta:0];
        need(left && right[0] && right[1] && changing && out && kernel, "matrix resources");
        cpu.resize(size_t(m)*q); host.resize(size_t(m)*q);
        docBits = packBits((const float *)a.contents,m);
        need(BLASSetThreading(BLAS_THREADING_MULTI_THREADED) == 0, "Accelerate automatic multithreading");
        readNs = t1-t0; setupNs = now()-t1;
    }
    const float *querySource(int bank) const {
        return (const float *)queryFile.bytes + size_t(bank)*BankRows*K;
    }
    Tick run(bool gpu, int bank, bool streaming, bool check) {
        @autoreleasepool {
            Tick x{};
            x.start = now();
            // On a stream, Accelerate can consume contiguous caller memory directly.
            // Metal stages the caller's bytes into one reusable shared buffer.
            if (gpu && streaming) memcpy(stream.contents, querySource(bank), size_t(q)*K*4);
            x.packed = now();
            if (gpu) {
                id<MTLCommandBuffer> cmd = [queue commandBuffer]; need(cmd != nil, "command buffer");
                [kernel encodeToCommandBuffer:cmd leftMatrix:left rightMatrix:(streaming ? changing : right[bank]) resultMatrix:out];
                [cmd commit]; x.submitted = now();
                [cmd waitUntilCompleted]; x.completed = now();
                need(cmd.status == MTLCommandBufferStatusCompleted && cmd.error == nil, "GPU command did not complete");
                need(cmd.GPUEndTime >= cmd.GPUStartTime && cmd.GPUStartTime > 0, "missing GPU timestamps");
                x.gpuStart = (uint64_t)llround(cmd.GPUStartTime*1e9);
                x.gpuEnd = (uint64_t)llround(cmd.GPUEndTime*1e9);
                const char *src = (const char *)c.contents;
                if (cStride == size_t(q)*4) memcpy(host.data(),src,outBytes);
                else for (int i=0; i<m; ++i) memcpy(host.data()+size_t(i)*q, src+size_t(i)*cStride, size_t(q)*4);
            } else {
                const float *qt = streaming ? querySource(bank) : (const float *)t[bank].contents;
                x.submitted = now();
                cblas_sgemm(CblasRowMajor,CblasNoTrans,CblasTrans,m,q,K,1.0f,(const float *)a.contents,K,
                            qt,K,0.0f,cpu.data(),q);
                x.completed = now();
                memcpy(host.data(),cpu.data(),outBytes);
            }
            x.materialized = now();
            if (check) need(!memcmp(host.data(),reference[bank].data(),outBytes), "complete output differs");
            x.checked = now();
            return x;
        }
    }
    NSDictionary *initialize() {
        NSMutableArray *proof = [NSMutableArray array];
        for (int bank=0; bank<2; ++bank) {
            run(false,bank,false,false); reference[bank] = host;
            run(true,bank,false,true);
            NSMutableArray *tops = [NSMutableArray array], *samples = [NSMutableArray array];
            vector<int> queryIds{0, q/2, q-1};
            std::sort(queryIds.begin(),queryIds.end());
            queryIds.erase(std::unique(queryIds.begin(),queryIds.end()),queryIds.end());
            for (int col : queryIds) {
                vector<std::pair<int,int>> ranking;
                for (int row=0; row<m; ++row) {
                    int score = dot(docBits,queryBits[bank],row,col);
                    need(host[size_t(row)*q+col] == score,"integer dot disagrees");
                    ranking.emplace_back(-score,row);
                }
                std::partial_sort(ranking.begin(),ranking.begin()+5,ranking.end());
                NSMutableArray *hits = [NSMutableArray array];
                for (int j=0;j<5;++j) [hits addObject:@[@(ranking[j].second),@(-ranking[j].first)]];
                [tops addObject:@{@"query":@(col), @"top5":hits}];
            }
            for (int k=0;k<16;++k) {
                int i=(k*787+17)%m, j=(k*307+3)%q;
                int score=dot(docBits,queryBits[bank],i,j);
                need(host[size_t(i)*q+j] == score,"sample differs");
                [samples addObject:@[@(i),@(j),@(score)]];
            }
            vector<uint16_t> exact(host.size());
            for (size_t i=0;i<host.size();++i) {
                need(host[i] >= 0 && host[i] <= K && host[i] == floorf(host[i]), "non-integral output");
                exact[i] = (uint16_t)host[i];
            }
            [proof addObject:@{@"bank":@(bank),@"cells":@(size_t(m)*q),@"sha256":hashBytes(host.data(),outBytes),
                              @"u16_sha256":hashBytes(exact.data(),exact.size()*2),
                              @"top":tops,@"samples":samples}];
        }
        need(memcmp(reference[0].data(),reference[1].data(),outBytes) != 0,"two inputs produced identical outputs");
        return @{@"device":dev.name,@"unified":@(dev.hasUnifiedMemory),
                 @"m":@(m),@"k":@(K),@"q":@(q),@"gpu_row_stride":@(cStride),@"input_and_device_ns":@(readNs),
                 @"prepare_and_bitpack_ns":@(setupNs),@"automatic_blas_threads":@(BLASGetThreading()==BLAS_THREADING_MULTI_THREADED),
                 @"proof":proof};
    }
};
int main(int argc, const char **argv) {
    @autoreleasepool { try {
        need(argc==4,"usage: Batch inputs M Q");
        Batch b(atoi(argv[2]),atoi(argv[3]),[NSString stringWithUTF8String:argv[1]]);
        emit(b.initialize());
        std::string line;
        while (std::getline(std::cin,line)) { @autoreleasepool {
            if (line=="quit") break;
            std::istringstream in(line); std::string method;
            int bank, streaming, count; in>>method>>bank>>streaming>>count;
            need(in && (method=="gpu" || method=="cpu") && (bank==0 || bank==1) && (streaming==0 || streaming==1)
                 && count>0 && count<=32768,"invalid batch request");
            NSMutableArray *trials = [NSMutableArray arrayWithCapacity:count];
            for (int i=0;i<count;++i) {
                Tick x=b.run(method=="gpu",(bank+i)%2,streaming,true);
                [trials addObject:@[@(x.start),@(x.packed),@(x.submitted),@(x.completed),@(x.materialized),
                                    @(x.checked),@(x.gpuStart),@(x.gpuEnd)]];
            }
            emit(@{@"method":[NSString stringWithUTF8String:method.c_str()],@"first_bank":@(bank),
                   @"streaming":@(streaming),@"trials":trials});
        }}
    } catch(const std::exception &e) { fprintf(stderr,"%s\n",e.what()); return 1; } }
    return 0;
}
