// FLA 投屏广播扩展 (ReplayKit Broadcast Upload Extension)
// 系统录制整个 iPhone 屏幕 → 缩放 + JPEG → WebSocket 二进制帧 ('C' + JPEG) → 服务器中继 → 电脑
// 流控与安卓一致: 电脑显示完回 {"type":"ack","ch":"C"} 才发下一帧 (1.5s 超时兜底)
// 注意: 扩展内存上限约 50MB, 只保留一个 CIContext, 帧不排队
#import <ReplayKit/ReplayKit.h>
#import <CoreImage/CoreImage.h>
#import <UIKit/UIKit.h>

static NSString *const kGroup = @"group.top.clrv.fla";  // optional; a free profile may not authorize this suite

@interface SampleHandler : RPBroadcastSampleHandler <NSURLSessionWebSocketDelegate>
@property (nonatomic, strong) NSURLSession *urlSession;
@property (nonatomic, strong) NSURLSessionWebSocketTask *ws;
@property (nonatomic, strong) CIContext *ci;
@property (nonatomic, strong) dispatch_queue_t q;
@property (nonatomic, copy) NSString *sid, *code, *server;
@property (atomic) BOOL open, busy;
@property (atomic) double pendingAt, lastSent;
@property (nonatomic, strong) NSUserDefaults *group;
@property (nonatomic) int fails;
@end

@implementation SampleHandler

- (void)state:(NSString *)s {
    [self.group setObject:s forKey:@"state"];
    [self.group setDouble:[NSDate date].timeIntervalSince1970 forKey:@"alive"];
}

- (void)broadcastStartedWithSetupInfo:(NSDictionary<NSString *, NSObject *> *)setupInfo {
    self.q = dispatch_queue_create("fla.cast", DISPATCH_QUEUE_SERIAL);
    self.ci = [CIContext contextWithOptions:@{kCIContextWorkingColorSpace: [NSNull null], kCIContextUseSoftwareRenderer: @NO}];
    self.group = [[NSUserDefaults alloc] initWithSuiteName:kGroup];
    self.sid = [self.group stringForKey:@"sid"];
    self.code = [self.group stringForKey:@"code"];
    self.server = [self.group stringForKey:@"server"];
    self.urlSession = [NSURLSession sessionWithConfiguration:NSURLSessionConfiguration.defaultSessionConfiguration delegate:self delegateQueue:nil];
    [self state:@"正在连接电脑…"];
    if (self.sid.length && self.code.length && self.server.length) [self connect];
    else [self lookup:0];     // Personal/free profiles may lack App Group; retrieve the session by vendor ID.
}

- (void)lookup:(int)i {
    NSArray *servers = @[@"https://t.clrv.top", @"https://t.fyx.best"];
    if (i >= (int)servers.count) {
        NSError *e = [NSError errorWithDomain:@"FLA" code:1 userInfo:@{NSLocalizedFailureReasonErrorKey: @"未找到电脑：请先在 FLA App 里连接电脑，再点「整个手机屏幕投到电脑」"}];
        [self finishBroadcastWithError:e];
        return;
    }
    NSString *vid = UIDevice.currentDevice.identifierForVendor.UUIDString ?: @"";
    NSURL *u = [NSURL URLWithString:[NSString stringWithFormat:@"%@/api/remote/ios/session?vid=%@", servers[i], vid]];
    [[self.urlSession dataTaskWithURL:u completionHandler:^(NSData *data, NSURLResponse *r, NSError *err) {
        NSDictionary *d = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if ([(NSHTTPURLResponse *)r statusCode] == 200 && [d isKindOfClass:NSDictionary.class] && d[@"sid"]) {
            self.sid = d[@"sid"]; self.code = d[@"code"]; self.server = servers[i];
            [self connect];
        } else {
            [self lookup:i + 1];
        }
    }] resume];
}

- (void)connect {
    NSString *base = [self.server stringByReplacingOccurrencesOfString:@"https://" withString:@"wss://"];
    base = [base stringByReplacingOccurrencesOfString:@"http://" withString:@"ws://"];
    NSURL *u = [NSURL URLWithString:[NSString stringWithFormat:@"%@/api/remote/hub/%@?code=%@&role=phone", base, self.sid, self.code]];
    self.ws = [self.urlSession webSocketTaskWithURL:u];
    self.ws.maximumMessageSize = 8 * 1024 * 1024;
    [self.ws resume];
    [self receive];
}

- (void)URLSession:(NSURLSession *)s webSocketTask:(NSURLSessionWebSocketTask *)t didOpenWithProtocol:(NSString *)p {
    if (t != self.ws) return;
    self.open = YES; self.fails = 0; self.pendingAt = 0;
    [self state:@"正在整屏投屏"];
    NSString *hello = @"{\"action\":\"cast_start\",\"data\":{\"src\":\"ios\"}}";
    [t sendMessage:[[NSURLSessionWebSocketMessage alloc] initWithString:hello] completionHandler:^(NSError *e) {}];
    __weak SampleHandler *ws = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [ws ping:t]; });
}

- (void)ping:(NSURLSessionWebSocketTask *)t {
    if (t != self.ws || !self.open) return;
    [t sendMessage:[[NSURLSessionWebSocketMessage alloc] initWithString:@"ping"] completionHandler:^(NSError *e) {}];
    [self state:@"正在整屏投屏"];
    __weak SampleHandler *ws = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [ws ping:t]; });
}

- (void)URLSession:(NSURLSession *)s webSocketTask:(NSURLSessionWebSocketTask *)t didCloseWithCode:(NSURLSessionWebSocketCloseCode)c reason:(NSData *)r {
    if (t == self.ws) [self dropped];
}
- (void)URLSession:(NSURLSession *)s task:(NSURLSessionTask *)t didCompleteWithError:(NSError *)e {
    if (t == self.ws && e) [self dropped];
}
- (void)dropped {
    self.open = NO;
    [self state:@"连接断开，正在重连…"];
    self.fails++;
    double delay = MIN(5.0, 0.8 * self.fails);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(delay * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ [self connect]; });
}

- (void)receive {
    NSURLSessionWebSocketTask *t = self.ws;
    __weak SampleHandler *ws = self;
    [t receiveMessageWithCompletionHandler:^(NSURLSessionWebSocketMessage *m, NSError *e) {
        if (e || t != ws.ws) return;
        if (m.type == NSURLSessionWebSocketMessageTypeString && [m.string containsString:@"\"ack\""] && [m.string containsString:@"\"C\""])
            ws.pendingAt = 0;
        [ws receive];
    }];
}

- (void)processSampleBuffer:(CMSampleBufferRef)sb withType:(RPSampleBufferType)type {
    if (type != RPSampleBufferTypeVideo || !self.open || self.busy) return;
    double now = CACurrentMediaTime();
    if (self.pendingAt > 0 && now - self.pendingAt < 1.5) return;   // 等电脑显示完
    if (now - self.lastSent < 0.05) return;                         // ≤ 20 fps
    CVPixelBufferRef px = CMSampleBufferGetImageBuffer(sb);
    if (!px) return;
    self.busy = YES;
    self.lastSent = now;
    CVPixelBufferRetain(px);
    NSNumber *orient = (__bridge NSNumber *)CMGetAttachment(sb, (__bridge CFStringRef)RPVideoSampleOrientationKey, NULL);
    CGImagePropertyOrientation o = orient ? (CGImagePropertyOrientation)orient.unsignedIntValue : kCGImagePropertyOrientationUp;
    dispatch_async(self.q, ^{
        @autoreleasepool {
            CIImage *img = [[CIImage imageWithCVPixelBuffer:px] imageByApplyingCGOrientation:o];
            CVPixelBufferRelease(px);
            CGFloat side = MAX(img.extent.size.width, img.extent.size.height);
            CGFloat k = MIN(1.0, 1440.0 / side);
            if (k < 1.0) img = [img imageByApplyingTransform:CGAffineTransformMakeScale(k, k)];
            img = [img imageByApplyingTransform:CGAffineTransformMakeTranslation(-img.extent.origin.x, -img.extent.origin.y)];
            CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
            NSData *jpg = [self.ci JPEGRepresentationOfImage:img colorSpace:cs
                                                     options:@{(__bridge NSString *)kCGImageDestinationLossyCompressionQuality: @0.6}];
            CGColorSpaceRelease(cs);
            if (jpg.length && self.open) {
                NSMutableData *out = [NSMutableData dataWithCapacity:jpg.length + 1];
                uint8_t c = 'C';
                [out appendBytes:&c length:1];
                [out appendData:jpg];
                self.pendingAt = CACurrentMediaTime();
                [self.ws sendMessage:[[NSURLSessionWebSocketMessage alloc] initWithData:out] completionHandler:^(NSError *e) {}];
            }
            self.busy = NO;
        }
    });
}

- (void)broadcastPaused { self.open = NO; [self state:@"已暂停"]; }
- (void)broadcastResumed { self.open = YES; [self state:@"正在整屏投屏"]; }
- (void)broadcastFinished {
    self.open = NO;
    if (self.ws) {
        NSString *bye = @"{\"action\":\"cast_stop\",\"data\":{}}";
        [self.ws sendMessage:[[NSURLSessionWebSocketMessage alloc] initWithString:bye] completionHandler:^(NSError *e) {}];
        [self.ws cancelWithCloseCode:NSURLSessionWebSocketCloseCodeNormalClosure reason:nil];
    }
    [self.group setDouble:0 forKey:@"alive"];
}
@end
