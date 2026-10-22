// FLA 手机端 (iOS): UIKit 原生首页/投屏配对页 + WKWebView 功能页 + 原生扫码/ReplayKit 整屏投屏
// AI、课件、交流等服务端模块保留为独立 Web 功能页; 主首页和连接/投屏流程不依赖网页
#import <UIKit/UIKit.h>
#import <WebKit/WebKit.h>
#import <AVFoundation/AVFoundation.h>
#import <ReplayKit/ReplayKit.h>

static NSString *const kVersion = @"1.1.1";
static NSString *const kGroup = @"group.top.clrv.fla";   // optional: only available when the signing profile grants App Groups
static NSString *const kExt = @"top.clrv.fla.app.broadcast";

static NSString *JSQuote(NSString *s) {
    NSData *d = [NSJSONSerialization dataWithJSONObject:@[s ?: @""] options:0 error:nil];
    NSString *a = [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding];
    return [a substringWithRange:NSMakeRange(1, a.length - 2)];
}

// ================================================================== 扫码
@interface ScanVC : UIViewController <AVCaptureMetadataOutputObjectsDelegate>
@property (nonatomic, strong) AVCaptureSession *session;
@property (nonatomic, copy) void (^done)(NSString *text);
@end

@implementation ScanVC
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.blackColor;
    self.session = [AVCaptureSession new];
    AVCaptureDevice *dev = [AVCaptureDevice defaultDeviceWithMediaType:AVMediaTypeVideo];
    AVCaptureDeviceInput *input = dev ? [AVCaptureDeviceInput deviceInputWithDevice:dev error:nil] : nil;
    if (input && [self.session canAddInput:input]) [self.session addInput:input];
    AVCaptureMetadataOutput *out = [AVCaptureMetadataOutput new];
    if ([self.session canAddOutput:out]) {
        [self.session addOutput:out];
        [out setMetadataObjectsDelegate:self queue:dispatch_get_main_queue()];
        if ([out.availableMetadataObjectTypes containsObject:AVMetadataObjectTypeQRCode])
            out.metadataObjectTypes = @[AVMetadataObjectTypeQRCode];
    }
    AVCaptureVideoPreviewLayer *pl = [AVCaptureVideoPreviewLayer layerWithSession:self.session];
    pl.videoGravity = AVLayerVideoGravityResizeAspectFill;
    pl.frame = self.view.bounds;
    [self.view.layer addSublayer:pl];

    CGSize sz = self.view.bounds.size;
    CGFloat w = MIN(sz.width, sz.height) * 0.68;
    UIView *box = [[UIView alloc] initWithFrame:CGRectMake((sz.width - w) / 2, (sz.height - w) / 2, w, w)];
    box.layer.borderColor = UIColor.whiteColor.CGColor;
    box.layer.borderWidth = 3;
    box.layer.cornerRadius = 24;
    [self.view addSubview:box];

    UILabel *tip = [UILabel new];
    tip.text = input ? @"将电脑上的二维码放入框内" : @"无法打开相机：请在 设置 → FLA 中允许使用相机";
    tip.textColor = UIColor.whiteColor;
    tip.numberOfLines = 2;
    tip.textAlignment = NSTextAlignmentCenter;
    tip.frame = CGRectMake(20, CGRectGetMaxY(box.frame) + 20, sz.width - 40, 48);
    [self.view addSubview:tip];

    UIButton *x = [UIButton buttonWithType:UIButtonTypeSystem];
    [x setTitle:@"取消" forState:UIControlStateNormal];
    x.titleLabel.font = [UIFont systemFontOfSize:18 weight:UIFontWeightSemibold];
    [x setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    x.backgroundColor = [UIColor colorWithWhite:1 alpha:0.18];
    x.layer.cornerRadius = 26;
    x.frame = CGRectMake((sz.width - 120) / 2, sz.height - 120, 120, 52);
    [x addTarget:self action:@selector(cancel) forControlEvents:UIControlEventTouchUpInside];
    [self.view addSubview:x];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{ [self.session startRunning]; });
}
- (void)cancel {
    [self.session stopRunning];
    [self dismissViewControllerAnimated:YES completion:nil];
}
- (void)captureOutput:(AVCaptureOutput *)o didOutputMetadataObjects:(NSArray *)objs fromConnection:(AVCaptureConnection *)c {
    for (AVMetadataObject *m in objs) {
        if (![m isKindOfClass:AVMetadataMachineReadableCodeObject.class]) continue;
        NSString *s = ((AVMetadataMachineReadableCodeObject *)m).stringValue;
        if (!s.length) continue;
        [self.session stopRunning];
        [[[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleMedium] impactOccurred];
        void (^d)(NSString *) = self.done;
        [self dismissViewControllerAnimated:YES completion:^{ if (d) d(s); }];
        return;
    }
}
@end

// ================================================================== 主界面
@interface MainVC : UIViewController <WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate>
@property (nonatomic, strong) WKWebView *web;
@property (nonatomic, strong) UIView *picker;
@property (nonatomic, strong) UIView *nativeHome;
@property (nonatomic, strong) UILabel *homeStatusLabel;
@property (nonatomic, strong) UILabel *castStatusLabel;
@property (nonatomic, strong) UITextField *pairCodeField;
@property (nonatomic, copy) NSString *status;
@property (nonatomic, copy) NSString *activeServer;
@property (nonatomic, copy) NSString *sid;
@property (nonatomic, copy) NSString *pairCode;
@property (nonatomic, copy) NSString *server;
@property (nonatomic, copy) NSString *activePage;
@property (nonatomic) BOOL nativePairing;
- (void)handleScanText:(NSString *)text;
- (void)showMessage:(NSString *)message;
- (void)showCast;
- (void)goHome;
- (void)openCastWeb;
- (void)pair:(NSString *)srv code:(NSString *)c;
- (void)startScreen:(NSString *)sid code:(NSString *)code server:(NSString *)srv;
- (void)tapPicker;
- (BOOL)trusted;
@end

@implementation MainVC
- (UIStatusBarStyle)preferredStatusBarStyle { return UIStatusBarStyleLightContent; }

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor colorWithRed:0.047 green:0.051 blue:0.063 alpha:1];
    self.activeServer = [[NSUserDefaults standardUserDefaults] stringForKey:@"fla_server"] ?: @"https://t.clrv.top";
    if (![self.activeServer isEqualToString:@"https://t.clrv.top"] && ![self.activeServer isEqualToString:@"https://t.fyx.best"])
        self.activeServer = @"https://t.clrv.top";
    self.activePage = @"home";

    WKWebViewConfiguration *cfg = [WKWebViewConfiguration new];
    cfg.allowsInlineMediaPlayback = YES;
    cfg.mediaTypesRequiringUserActionForPlayback = WKAudiovisualMediaTypeNone;
    cfg.applicationNameForUserAgent = [NSString stringWithFormat:@"Mobile/15E148 Safari/604.1 FLA-App/%@ FLA-iOS", kVersion];
    WKUserContentController *uc = [WKUserContentController new];
    [uc addScriptMessageHandler:self name:@"fla"];
    NSString *shim = [NSString stringWithFormat:@
        "(function(){if(window.FLA)return;"
        "var p=function(m,a){window.webkit.messageHandlers.fla.postMessage({m:m,a:a||[]});};"
        "window.FLA={ios:true,version:function(){return '%@';},"
        "pair:function(s,c){p('pair',[s,c]);},"
        "startScreen:function(s,c,v){p('startScreen',[s,c,v]);},"
        "stopScreen:function(){p('stopScreen');},"
        "home:function(){p('home');},vibrate:function(){p('vibrate');},"
        "scanNative:function(){p('scan');}};})();", kVersion];
    [uc addUserScript:[[WKUserScript alloc] initWithSource:shim injectionTime:WKUserScriptInjectionTimeAtDocumentStart forMainFrameOnly:YES]];
    cfg.userContentController = uc;
    self.web = [[WKWebView alloc] initWithFrame:CGRectZero configuration:cfg];
    self.web.opaque = NO;
    self.web.backgroundColor = self.view.backgroundColor;
    self.web.scrollView.backgroundColor = self.view.backgroundColor;
    self.web.navigationDelegate = self;
    self.web.UIDelegate = self;
    self.web.allowsBackForwardNavigationGestures = NO;

    [self renderHome];
    // ReplayKit 的系统广播选择器保留为隐藏按钮，由原生投屏页触发。
    if (@available(iOS 12.0, *)) {
        RPSystemBroadcastPickerView *pk = [[RPSystemBroadcastPickerView alloc] initWithFrame:CGRectMake(-200, -200, 60, 60)];
        pk.preferredExtension = kExt;
        pk.showsMicrophoneButton = NO;
        [self.view addSubview:pk];
        self.picker = pk;
    }
    [NSTimer scheduledTimerWithTimeInterval:1.5 repeats:YES block:^(NSTimer *t) { [self pollBroadcast]; }];
}

- (UIColor *)flaColor:(NSString *)hex {
    unsigned value = 0;
    NSScanner *scanner = [NSScanner scannerWithString:[hex stringByReplacingOccurrencesOfString:@"#" withString:@""]];
    [scanner scanHexInt:&value];
    return [UIColor colorWithRed:((value >> 16) & 255) / 255.0 green:((value >> 8) & 255) / 255.0 blue:(value & 255) / 255.0 alpha:1];
}

- (UILabel *)textLabel:(NSString *)text size:(CGFloat)size color:(UIColor *)color weight:(UIFontWeight)weight {
    UILabel *label = [UILabel new];
    label.translatesAutoresizingMaskIntoConstraints = NO;
    label.text = text;
    label.textColor = color;
    label.font = [UIFont systemFontOfSize:size weight:weight];
    label.numberOfLines = 0;
    return label;
}

- (UIButton *)actionButton:(NSString *)title primary:(BOOL)primary action:(SEL)action {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    button.translatesAutoresizingMaskIntoConstraints = NO;
    [button setTitle:title forState:UIControlStateNormal];
    [button setTitleColor:primary ? [self flaColor:@"#101722"] : [self flaColor:@"#edf0f4"] forState:UIControlStateNormal];
    button.titleLabel.font = [UIFont systemFontOfSize:15 weight:UIFontWeightSemibold];
    button.titleLabel.numberOfLines = 0;
    button.backgroundColor = primary ? [self flaColor:@"#c7d8f3"] : [self flaColor:@"#242831"];
    button.layer.cornerRadius = 14;
    button.layer.borderWidth = 1;
    button.layer.borderColor = (primary ? [self flaColor:@"#c7d8f3"] : [self flaColor:@"#363b45"]).CGColor;
    [button addTarget:self action:action forControlEvents:UIControlEventTouchUpInside];
    return button;
}

- (UIButton *)featureTile:(NSString *)icon title:(NSString *)title subtitle:(NSString *)subtitle tag:(NSInteger)tag {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    button.translatesAutoresizingMaskIntoConstraints = NO;
    button.tag = tag;
    button.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    button.contentVerticalAlignment = UIControlContentVerticalAlignmentCenter;
    button.contentEdgeInsets = UIEdgeInsetsMake(14, 14, 14, 14);
    button.backgroundColor = [self flaColor:@"#171a20"];
    button.layer.cornerRadius = 18;
    button.layer.borderWidth = 1;
    button.layer.borderColor = [self flaColor:@"#292d35"].CGColor;
    button.titleLabel.numberOfLines = 0;
    button.titleLabel.lineBreakMode = NSLineBreakByWordWrapping;
    NSMutableAttributedString *titleText = [[NSMutableAttributedString alloc] initWithString:[NSString stringWithFormat:@"%@  %@\n%@", icon, title, subtitle]];
    [titleText addAttributes:@{NSFontAttributeName: [UIFont systemFontOfSize:15 weight:UIFontWeightSemibold], NSForegroundColorAttributeName: [self flaColor:@"#f4f5f7"]} range:NSMakeRange(0, icon.length + title.length + 2)];
    NSRange subtitleRange = NSMakeRange(icon.length + title.length + 3, subtitle.length);
    [titleText addAttributes:@{NSFontAttributeName: [UIFont systemFontOfSize:11 weight:UIFontWeightRegular], NSForegroundColorAttributeName: [self flaColor:@"#9297a2"]} range:subtitleRange];
    NSMutableParagraphStyle *paragraph = [NSMutableParagraphStyle new];
    paragraph.lineSpacing = 5;
    [titleText addAttribute:NSParagraphStyleAttributeName value:paragraph range:NSMakeRange(0, titleText.length)];
    [button setAttributedTitle:titleText forState:UIControlStateNormal];
    [button addTarget:self action:@selector(openFeatureButton:) forControlEvents:UIControlEventTouchUpInside];
    [button.heightAnchor constraintGreaterThanOrEqualToConstant:104].active = YES;
    return button;
}

- (UIStackView *)columnInPanel:(UIView *)panel {
    UIScrollView *scroll = [UIScrollView new];
    scroll.translatesAutoresizingMaskIntoConstraints = NO;
    scroll.alwaysBounceVertical = YES;
    scroll.contentInset = UIEdgeInsetsMake(10, 0, 28, 0);
    [panel addSubview:scroll];
    [NSLayoutConstraint activateConstraints:@[
        [scroll.leadingAnchor constraintEqualToAnchor:panel.leadingAnchor],
        [scroll.trailingAnchor constraintEqualToAnchor:panel.trailingAnchor],
        [scroll.topAnchor constraintEqualToAnchor:panel.safeAreaLayoutGuide.topAnchor],
        [scroll.bottomAnchor constraintEqualToAnchor:panel.safeAreaLayoutGuide.bottomAnchor]
    ]];
    UIStackView *stack = [[UIStackView alloc] init];
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    stack.axis = UILayoutConstraintAxisVertical;
    stack.spacing = 14;
    [scroll addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor constant:20],
        [stack.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor constant:-20],
        [stack.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor],
        [stack.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor],
        [stack.widthAnchor constraintEqualToAnchor:scroll.frameLayoutGuide.widthAnchor constant:-40]
    ]];
    return stack;
}

- (UIButton *)rowTile:(NSString *)icon title:(NSString *)title subtitle:(NSString *)subtitle tag:(NSInteger)tag {
    return [self featureTile:icon title:title subtitle:subtitle tag:tag];
}

- (void)renderHome {
    self.activePage = @"home";
    [self.web stopLoading];
    [self.nativeHome removeFromSuperview];
    UIView *panel = [UIView new];
    panel.translatesAutoresizingMaskIntoConstraints = NO;
    panel.backgroundColor = [self flaColor:@"#0c0d10"];
    [self.view addSubview:panel];
    [NSLayoutConstraint activateConstraints:@[
        [panel.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [panel.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
        [panel.topAnchor constraintEqualToAnchor:self.view.topAnchor],
        [panel.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor]
    ]];
    self.nativeHome = panel;
    UIStackView *stack = [self columnInPanel:panel];
    UILabel *title = [self textLabel:@"FLA 课堂助手" size:28 color:[self flaColor:@"#f6f7f9"] weight:UIFontWeightBold];
    [stack addArrangedSubview:title];
    UILabel *subtitle = [self textLabel:@"课件、课堂交流、AI 助教，一站使用" size:13 color:[self flaColor:@"#999eaa"] weight:UIFontWeightRegular];
    [stack addArrangedSubview:subtitle];

    UIView *statusCard = [UIView new];
    statusCard.translatesAutoresizingMaskIntoConstraints = NO;
    statusCard.backgroundColor = [self flaColor:@"#1c2028"];
    statusCard.layer.cornerRadius = 18;
    statusCard.layer.borderWidth = 1;
    statusCard.layer.borderColor = [self flaColor:@"#2c333e"].CGColor;
    UIStackView *statusStack = [[UIStackView alloc] initWithArrangedSubviews:@[
        [self textLabel:@"课堂状态" size:12 color:[self flaColor:@"#a2a8b3"] weight:UIFontWeightMedium]
    ]];
    statusStack.translatesAutoresizingMaskIntoConstraints = NO;
    statusStack.axis = UILayoutConstraintAxisVertical;
    statusStack.spacing = 5;
    self.homeStatusLabel = [self textLabel:self.status ?: @"准备就绪 · 连接电脑即可开始" size:13 color:[self flaColor:@"#eef0f4"] weight:UIFontWeightSemibold];
    [statusStack addArrangedSubview:self.homeStatusLabel];
    [statusCard addSubview:statusStack];
    [NSLayoutConstraint activateConstraints:@[
        [statusStack.leadingAnchor constraintEqualToAnchor:statusCard.leadingAnchor constant:16],
        [statusStack.trailingAnchor constraintEqualToAnchor:statusCard.trailingAnchor constant:-16],
        [statusStack.topAnchor constraintEqualToAnchor:statusCard.topAnchor constant:14],
        [statusStack.bottomAnchor constraintEqualToAnchor:statusCard.bottomAnchor constant:-14]
    ]];
    [stack addArrangedSubview:statusCard];

    [stack addArrangedSubview:[self textLabel:@"常用功能" size:17 color:[self flaColor:@"#f1f2f4"] weight:UIFontWeightBold]];
    UIStackView *row1 = [[UIStackView alloc] initWithArrangedSubviews:@[
        [self rowTile:@"▣" title:@"课件库" subtitle:@"查看 · 上传 · 放映" tag:1],
        [self rowTile:@"✦" title:@"AI 助教" subtitle:@"备课 · 答疑 · 写作" tag:2]
    ]];
    row1.axis = UILayoutConstraintAxisHorizontal; row1.spacing = 10; row1.distribution = UIStackViewDistributionFillEqually;
    [stack addArrangedSubview:row1];
    UIStackView *row2 = [[UIStackView alloc] initWithArrangedSubviews:@[
        [self rowTile:@"◉" title:@"课堂交流" subtitle:@"同事消息与讨论" tag:3],
        [self rowTile:@"▤" title:@"教学论坛" subtitle:@"公告 · 经验分享" tag:4]
    ]];
    row2.axis = UILayoutConstraintAxisHorizontal; row2.spacing = 10; row2.distribution = UIStackViewDistributionFillEqually;
    [stack addArrangedSubview:row2];
    UIStackView *row3 = [[UIStackView alloc] initWithArrangedSubviews:@[
        [self rowTile:@"♙" title:@"个人中心" subtitle:@"账号与个人资料" tag:5],
        [UIView new]
    ]];
    row3.axis = UILayoutConstraintAxisHorizontal; row3.spacing = 10; row3.distribution = UIStackViewDistributionFillEqually;
    [stack addArrangedSubview:row3];
    UIButton *cast = [self actionButton:@"▱  手机投屏 / 课堂遥控\n连接教室电脑后开始投屏；其他功能仍可独立使用" primary:NO action:@selector(showCast)];
    cast.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    cast.contentEdgeInsets = UIEdgeInsetsMake(16, 17, 16, 17);
    cast.backgroundColor = [self flaColor:@"#202936"];
    cast.layer.borderColor = [self flaColor:@"#41526a"].CGColor;
    cast.titleLabel.textAlignment = NSTextAlignmentLeft;
    cast.titleLabel.font = [UIFont systemFontOfSize:15 weight:UIFontWeightSemibold];
    [stack addArrangedSubview:cast];
    [self pushStatus];
}

- (void)openFeatureButton:(UIButton *)button {
    NSString *route = @"library", *title = @"课件库";
    switch (button.tag) {
        case 2: route = @"ai"; title = @"AI 助教"; break;
        case 3: route = @"chat"; title = @"课堂交流"; break;
        case 4: route = @"forum"; title = @"教学论坛"; break;
        case 5: route = @"profile"; title = @"个人中心"; break;
    }
    [self openFeature:route title:title];
}

- (void)openFeature:(NSString *)route title:(NSString *)title {
    self.activePage = @"web";
    [self.nativeHome removeFromSuperview];
    self.nativeHome = nil;
    UIView *panel = [UIView new];
    panel.translatesAutoresizingMaskIntoConstraints = NO;
    panel.backgroundColor = [self flaColor:@"#0c0d10"];
    [self.view addSubview:panel];
    [NSLayoutConstraint activateConstraints:@[
        [panel.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [panel.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
        [panel.topAnchor constraintEqualToAnchor:self.view.topAnchor],
        [panel.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor]
    ]];
    self.nativeHome = panel;
    UIButton *back = [UIButton buttonWithType:UIButtonTypeSystem];
    back.translatesAutoresizingMaskIntoConstraints = NO;
    [back setTitle:@"‹  首页" forState:UIControlStateNormal];
    [back setTitleColor:[self flaColor:@"#d6deea"] forState:UIControlStateNormal];
    back.titleLabel.font = [UIFont systemFontOfSize:16 weight:UIFontWeightSemibold];
    back.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    [back addTarget:self action:@selector(goHome) forControlEvents:UIControlEventTouchUpInside];
    UILabel *heading = [self textLabel:title size:17 color:[self flaColor:@"#f5f6f8"] weight:UIFontWeightSemibold];
    UIView *bar = [UIView new]; bar.translatesAutoresizingMaskIntoConstraints = NO; bar.backgroundColor = [self flaColor:@"#15171c"];
    [panel addSubview:bar]; [bar addSubview:back]; [bar addSubview:heading]; [panel addSubview:self.web];
    self.web.translatesAutoresizingMaskIntoConstraints = NO;
    [NSLayoutConstraint activateConstraints:@[
        [bar.leadingAnchor constraintEqualToAnchor:panel.leadingAnchor], [bar.trailingAnchor constraintEqualToAnchor:panel.trailingAnchor],
        [bar.topAnchor constraintEqualToAnchor:panel.safeAreaLayoutGuide.topAnchor], [bar.heightAnchor constraintEqualToConstant:54],
        [back.leadingAnchor constraintEqualToAnchor:bar.leadingAnchor constant:16], [back.centerYAnchor constraintEqualToAnchor:bar.centerYAnchor],
        [back.widthAnchor constraintEqualToConstant:86],
        [heading.centerXAnchor constraintEqualToAnchor:bar.centerXAnchor], [heading.centerYAnchor constraintEqualToAnchor:bar.centerYAnchor],
        [self.web.leadingAnchor constraintEqualToAnchor:panel.leadingAnchor], [self.web.trailingAnchor constraintEqualToAnchor:panel.trailingAnchor],
        [self.web.topAnchor constraintEqualToAnchor:bar.bottomAnchor], [self.web.bottomAnchor constraintEqualToAnchor:panel.bottomAnchor]
    ]];
    NSString *base = self.activeServer ?: @"https://t.clrv.top";
    [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithFormat:@"%@/#/%@", base, route]]]];
}

- (void)showCast {
    self.activePage = @"cast";
    self.nativePairing = NO;
    [self.nativeHome removeFromSuperview];
    self.nativeHome = nil;
    UIView *panel = [UIView new];
    panel.translatesAutoresizingMaskIntoConstraints = NO;
    panel.backgroundColor = [self flaColor:@"#0c0d10"];
    [self.view addSubview:panel];
    [NSLayoutConstraint activateConstraints:@[
        [panel.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [panel.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
        [panel.topAnchor constraintEqualToAnchor:self.view.topAnchor],
        [panel.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor]
    ]];
    self.nativeHome = panel;
    UIStackView *stack = [self columnInPanel:panel];
    UIButton *back = [UIButton buttonWithType:UIButtonTypeSystem];
    [back setTitle:@"‹  返回首页" forState:UIControlStateNormal];
    [back setTitleColor:[self flaColor:@"#cbd5e1"] forState:UIControlStateNormal];
    back.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    [back addTarget:self action:@selector(goHome) forControlEvents:UIControlEventTouchUpInside];
    [stack addArrangedSubview:back];
    [stack addArrangedSubview:[self textLabel:@"手机投屏" size:27 color:[self flaColor:@"#f6f7f9"] weight:UIFontWeightBold]];
    [stack addArrangedSubview:[self textLabel:@"投屏是 FLA 的一项课堂功能，首页与其他功能仍可独立使用。" size:12 color:[self flaColor:@"#9297a2"] weight:UIFontWeightRegular]];
    UIView *statusCard = [UIView new]; statusCard.translatesAutoresizingMaskIntoConstraints = NO;
    statusCard.backgroundColor = [self flaColor:@"#1c2028"]; statusCard.layer.cornerRadius = 16;
    statusCard.layer.borderWidth = 1; statusCard.layer.borderColor = [self flaColor:@"#2c333e"].CGColor;
    self.castStatusLabel = [self textLabel:self.status ?: @"请先在电脑端打开「手机」面板" size:13 color:[self flaColor:@"#e8ebef"] weight:UIFontWeightSemibold];
    [statusCard addSubview:self.castStatusLabel];
    [NSLayoutConstraint activateConstraints:@[
        [self.castStatusLabel.leadingAnchor constraintEqualToAnchor:statusCard.leadingAnchor constant:16],
        [self.castStatusLabel.trailingAnchor constraintEqualToAnchor:statusCard.trailingAnchor constant:-16],
        [self.castStatusLabel.topAnchor constraintEqualToAnchor:statusCard.topAnchor constant:14],
        [self.castStatusLabel.bottomAnchor constraintEqualToAnchor:statusCard.bottomAnchor constant:-14]
    ]];
    [stack addArrangedSubview:statusCard];
    if (!self.sid.length || !self.pairCode.length || !self.server.length) {
        [stack addArrangedSubview:[self textLabel:@"连接教室电脑" size:17 color:[self flaColor:@"#f1f2f4"] weight:UIFontWeightBold]];
        [stack addArrangedSubview:[self textLabel:@"在电脑端工具栏点「手机」，再扫码或输入 4 位配对码。" size:12 color:[self flaColor:@"#9297a2"] weight:UIFontWeightRegular]];
        UIStackView *servers = [[UIStackView alloc] initWithArrangedSubviews:@[
            [self serverButton:@"线路一 · t.clrv.top" url:@"https://t.clrv.top"],
            [self serverButton:@"线路二 · t.fyx.best" url:@"https://t.fyx.best"]
        ]];
        servers.axis = UILayoutConstraintAxisHorizontal; servers.spacing = 8; servers.distribution = UIStackViewDistributionFillEqually;
        [stack addArrangedSubview:servers];
        self.pairCodeField = [UITextField new];
        self.pairCodeField.translatesAutoresizingMaskIntoConstraints = NO;
        self.pairCodeField.placeholder = @"输入电脑上的 4 位配对码";
        self.pairCodeField.keyboardType = UIKeyboardTypeNumberPad;
        self.pairCodeField.textColor = UIColor.whiteColor;
        self.pairCodeField.tintColor = [self flaColor:@"#c7d8f3"];
        self.pairCodeField.textAlignment = NSTextAlignmentCenter;
        self.pairCodeField.font = [UIFont systemFontOfSize:20 weight:UIFontWeightSemibold];
        self.pairCodeField.backgroundColor = [self flaColor:@"#171a20"];
        self.pairCodeField.layer.cornerRadius = 14;
        self.pairCodeField.layer.borderWidth = 1;
        self.pairCodeField.layer.borderColor = [self flaColor:@"#363b45"].CGColor;
        [stack addArrangedSubview:self.pairCodeField];
        [self.pairCodeField.heightAnchor constraintEqualToConstant:54].active = YES;
        [stack addArrangedSubview:[self actionButton:@"扫码连接" primary:NO action:@selector(scanFromNativeCast)]];
        [stack addArrangedSubview:[self actionButton:@"连接电脑" primary:YES action:@selector(pairFromNativeCast)]];
    } else {
        [stack addArrangedSubview:[self textLabel:[NSString stringWithFormat:@"已连接电脑 · 配对码 %@", self.pairCode] size:15 color:[self flaColor:@"#f1f2f4"] weight:UIFontWeightSemibold]];
        [stack addArrangedSubview:[self actionButton:@"开始整屏投屏" primary:YES action:@selector(startNativeScreen)]];
        [stack addArrangedSubview:[self actionButton:@"打开课堂遥控与观看" primary:NO action:@selector(openCastWeb)]];
        [stack addArrangedSubview:[self actionButton:@"结束手机投屏" primary:NO action:@selector(stopNativeScreen)]];
    }
    [self pushStatus];
}

- (UIButton *)serverButton:(NSString *)title url:(NSString *)url {
    UIButton *button = [self actionButton:title primary:[self.activeServer isEqualToString:url] action:@selector(selectServer:)];
    button.tag = [url isEqualToString:@"https://t.fyx.best"] ? 1 : 0;
    button.titleLabel.font = [UIFont systemFontOfSize:11 weight:UIFontWeightMedium];
    return button;
}

- (void)selectServer:(UIButton *)button {
    self.activeServer = button.tag ? @"https://t.fyx.best" : @"https://t.clrv.top";
    [[NSUserDefaults standardUserDefaults] setObject:self.activeServer forKey:@"fla_server"];
    [self showCast];
}

- (void)scanFromNativeCast { self.nativePairing = YES; [self scan]; }
- (void)pairFromNativeCast {
    self.nativePairing = YES;
    [self.pairCodeField resignFirstResponder];
    [self pair:self.activeServer code:self.pairCodeField.text ?: @""];
}
- (void)startNativeScreen {
    if (self.sid.length && self.pairCode.length && self.server.length)
        [self startScreen:self.sid code:self.pairCode server:self.server];
}
- (void)stopNativeScreen {
    self.status = @"请点屏幕左上角红色计时条 → 停止，或在控制中心停止录屏";
    [self tapPicker];
}
- (void)openCastWeb {
    if (!self.sid.length || !self.pairCode.length || !self.server.length) return;
    self.activePage = @"web";
    [self.nativeHome removeFromSuperview];
    self.nativeHome = nil;
    UIView *panel = [UIView new]; panel.translatesAutoresizingMaskIntoConstraints = NO; panel.backgroundColor = self.view.backgroundColor;
    [self.view addSubview:panel];
    [NSLayoutConstraint activateConstraints:@[[panel.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor], [panel.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor], [panel.topAnchor constraintEqualToAnchor:self.view.topAnchor], [panel.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor]]];
    self.nativeHome = panel;
    UIButton *back = [UIButton buttonWithType:UIButtonTypeSystem]; back.translatesAutoresizingMaskIntoConstraints = NO;
    [back setTitle:@"‹  返回" forState:UIControlStateNormal]; [back setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    [back addTarget:self action:@selector(showCast) forControlEvents:UIControlEventTouchUpInside];
    [panel addSubview:back]; [panel addSubview:self.web]; self.web.translatesAutoresizingMaskIntoConstraints = NO;
    [NSLayoutConstraint activateConstraints:@[[back.leadingAnchor constraintEqualToAnchor:panel.leadingAnchor constant:16], [back.topAnchor constraintEqualToAnchor:panel.safeAreaLayoutGuide.topAnchor constant:8], [back.heightAnchor constraintEqualToConstant:42], [self.web.leadingAnchor constraintEqualToAnchor:panel.leadingAnchor], [self.web.trailingAnchor constraintEqualToAnchor:panel.trailingAnchor], [self.web.topAnchor constraintEqualToAnchor:back.bottomAnchor], [self.web.bottomAnchor constraintEqualToAnchor:panel.bottomAnchor]]];
    [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithFormat:@"%@/cast.html?sid=%@&code=%@&tab=cast", self.server, self.sid, self.pairCode]]]];
}

- (void)goHome { [self renderHome]; }
- (void)js:(NSString *)code {
    if (!self.web) return;
    dispatch_async(dispatch_get_main_queue(), ^{ [self.web evaluateJavaScript:code completionHandler:nil]; });
}

- (void)pushStatus {
    NSUserDefaults *g = [[NSUserDefaults alloc] initWithSuiteName:kGroup];
    double alive = [g doubleForKey:@"alive"];
    BOOL on = alive > 0 && [NSDate date].timeIntervalSince1970 - alive < 4;
    NSString *s = self.status ?: @"整个 iPhone 屏幕实时投到电脑（PPT、相册、任何 App）";
    if (on) s = [g stringForKey:@"state"] ?: @"正在整屏投屏";
    if (self.homeStatusLabel) self.homeStatusLabel.text = s;
    if (self.castStatusLabel) self.castStatusLabel.text = s;
    [self js:[NSString stringWithFormat:@"window.flaStatus&&window.flaStatus(%@,%@)", JSQuote(s), on ? @"true" : @"false"]];
}
- (void)pollBroadcast { [self pushStatus]; }

- (BOOL)trusted {
    NSURL *u = self.web.URL;
    if (u.isFileURL) return YES;
    NSString *h = u.host ?: @"";
    return [h hasSuffix:@"clrv.top"] || [h hasSuffix:@"fyx.best"] || [h hasPrefix:@"192.168."] || [h hasPrefix:@"10."] || [h hasPrefix:@"172."];
}

- (void)userContentController:(WKUserContentController *)uc didReceiveScriptMessage:(WKScriptMessage *)msg {
    if (![msg.body isKindOfClass:NSDictionary.class]) return;
    NSString *m = msg.body[@"m"];
    NSArray *a = msg.body[@"a"];
    if ([m isEqualToString:@"pair"] && a.count >= 2) [self pair:a[0] code:a[1]];
    else if ([m isEqualToString:@"home"]) [self goHome];
    else if ([m isEqualToString:@"vibrate"]) [[[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleLight] impactOccurred];
    else if ([m isEqualToString:@"scan"]) [self scan];
    else if ([m isEqualToString:@"startScreen"] && a.count >= 3 && [self trusted]) [self startScreen:a[0] code:a[1] server:a[2]];
    else if ([m isEqualToString:@"stopScreen"]) {
        self.status = @"请点屏幕左上角红色计时条 → 停止，或在控制中心停止录屏";
        [self tapPicker];
    }
}

- (void)scan {
    ScanVC *v = [ScanVC new];
    v.modalPresentationStyle = UIModalPresentationFullScreen;
    __weak MainVC *ws = self;
    v.done = ^(NSString *t) {
        if (ws.nativePairing) [ws handleScanText:t];
        else [ws js:[NSString stringWithFormat:@"window.onScan&&window.onScan(%@)", JSQuote(t)]];
    };
    [self presentViewController:v animated:YES completion:nil];
}

- (void)get:(NSString *)url done:(void (^)(NSDictionary *d, NSInteger code))done {
    NSMutableURLRequest *r = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:url]];
    r.timeoutInterval = 10;
    [[[NSURLSession sharedSession] dataTaskWithRequest:r completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
        NSInteger code = [(NSHTTPURLResponse *)resp statusCode];
        NSDictionary *d = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{ done([d isKindOfClass:NSDictionary.class] ? d : nil, err ? -1 : code); });
    }] resume];
}

- (BOOL)allowedServer:(NSString *)value {
    NSURL *u = [NSURL URLWithString:value ?: @""];
    NSString *host = u.host.lowercaseString ?: @"";
    if (!([u.scheme.lowercaseString isEqualToString:@"http"] || [u.scheme.lowercaseString isEqualToString:@"https"])) return NO;
    if ([host isEqualToString:@"t.clrv.top"] || [host isEqualToString:@"t.fyx.best"]
        || [host hasSuffix:@".clrv.top"] || [host hasSuffix:@".fyx.best"]
        || [host hasPrefix:@"192.168."] || [host hasPrefix:@"10."]) return YES;
    return [host rangeOfString:@"^172\\.(1[6-9]|2[0-9]|3[01])\\." options:NSRegularExpressionSearch].location != NSNotFound;
}

- (void)handleScanText:(NSString *)text {
    NSString *value = [text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if ([value rangeOfString:@"^[0-9]{4}$" options:NSRegularExpressionSearch].location != NSNotFound) {
        [self pair:self.activeServer code:value];
        return;
    }
    NSURLComponents *parts = [NSURLComponents componentsWithString:value];
    NSString *c = nil, *srv = nil;
    for (NSURLQueryItem *item in parts.queryItems) {
        if ([item.name isEqualToString:@"code"]) c = item.value;
        else if ([item.name isEqualToString:@"srv"]) srv = item.value;
    }
    if (!srv.length && parts.scheme.length && parts.host.length) {
        NSString *authority = parts.port ? [NSString stringWithFormat:@"%@:%@", parts.host, parts.port] : parts.host;
        srv = [NSString stringWithFormat:@"%@://%@", parts.scheme, authority];
    }
    if (c.length == 4 && [self allowedServer:srv]) { [self pair:srv code:c]; return; }
    self.nativePairing = NO;
    [self showMessage:@"这不是 FLA 电脑端的配对二维码"];
}

- (void)pair:(NSString *)srv code:(NSString *)c {
    if (![self allowedServer:srv] || [c rangeOfString:@"^[0-9]{4}$" options:NSRegularExpressionSearch].location == NSNotFound) {
        if (self.nativePairing) { self.nativePairing = NO; [self showMessage:@"请输入有效的 4 位配对码"];
        } else [self js:[NSString stringWithFormat:@"onPair(false,'','','',%@)", JSQuote(@"配对码或线路无效")]];
        return;
    }
    NSString *enc = [c stringByAddingPercentEncodingWithAllowedCharacters:NSCharacterSet.URLPathAllowedCharacterSet];
    [self get:[NSString stringWithFormat:@"%@/api/remote/pair/%@", srv, enc] done:^(NSDictionary *d, NSInteger statusCode) {
        if (statusCode == 200 && d[@"session_id"]) {
            if (self.nativePairing) {
                self.nativePairing = NO;
                self.activeServer = srv;
                self.server = srv;
                self.pairCode = c;
                self.sid = d[@"session_id"];
                [[NSUserDefaults standardUserDefaults] setObject:srv forKey:@"fla_server"];
                self.status = @"已连接电脑，请选择投屏或课堂遥控";
                [self showCast];
            } else {
                [self js:[NSString stringWithFormat:@"onPair(true,%@,%@,%@)", JSQuote(srv), JSQuote(d[@"session_id"]), JSQuote(c)]];
            }
        } else {
            NSString *e = statusCode >= 400 && statusCode < 500 ? @"配对码无效，请确认电脑已打开「手机」面板" : @"网络不通，请检查网络或切换线路";
            if (self.nativePairing) { self.nativePairing = NO; [self showMessage:e]; }
            else [self js:[NSString stringWithFormat:@"onPair(false,'','','',%@)", JSQuote(e)]];
        }
    }];
}

- (void)showMessage:(NSString *)message {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"FLA" message:message preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}

/** 登记会话 (App Group + 服务器按设备 ID), 然后弹出系统「开始直播」面板 */
- (void)startScreen:(NSString *)sid code:(NSString *)code server:(NSString *)srv {
    if (!sid.length || !code.length || ![self allowedServer:srv]) {
        [self showMessage:@"请先连接到有效的教室电脑"];
        return;
    }
    NSUserDefaults *g = [[NSUserDefaults alloc] initWithSuiteName:kGroup];
    [g setObject:sid forKey:@"sid"];
    [g setObject:code forKey:@"code"];
    [g setObject:srv forKey:@"server"];
    [g synchronize];      // nil/unauthorized suite is safe; the extension falls back to server lookup
    NSString *vid = UIDevice.currentDevice.identifierForVendor.UUIDString ?: @"";
    NSMutableURLRequest *r = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[srv stringByAppendingString:@"/api/remote/ios/register"]]];
    r.HTTPMethod = @"POST";
    [r setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    r.HTTPBody = [NSJSONSerialization dataWithJSONObject:@{@"vid": vid, @"sid": sid, @"code": code} options:0 error:nil];
    self.status = @"在弹出的面板中选择「FLA 投屏」→ 开始直播";
    [self pushStatus];
    [[[NSURLSession sharedSession] dataTaskWithRequest:r completionHandler:^(NSData *d, NSURLResponse *resp, NSError *e) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self tapPicker]; });
    }] resume];
}

- (void)tapPicker {
    for (UIView *v in self.picker.subviews) {
        if ([v isKindOfClass:UIButton.class]) { [(UIButton *)v sendActionsForControlEvents:UIControlEventTouchUpInside]; return; }
    }
}

// ---- WebView
- (void)webView:(WKWebView *)w decidePolicyForNavigationAction:(WKNavigationAction *)a decisionHandler:(void (^)(WKNavigationActionPolicy))h {
    NSURL *u = a.request.URL;
    NSString *sc = u.scheme.lowercaseString;
    if ([sc isEqualToString:@"http"] || [sc isEqualToString:@"https"]) {
        if ([u.path hasPrefix:@"/api/app/"]) { [UIApplication.sharedApplication openURL:u options:@{} completionHandler:nil]; h(WKNavigationActionPolicyCancel); return; }
        h(WKNavigationActionPolicyAllow); return;
    }
    if ([sc isEqualToString:@"file"] || [sc isEqualToString:@"about"] || [sc isEqualToString:@"blob"] || [sc isEqualToString:@"data"]) { h(WKNavigationActionPolicyAllow); return; }
    [UIApplication.sharedApplication openURL:u options:@{} completionHandler:nil];
    h(WKNavigationActionPolicyCancel);
}
- (void)webView:(WKWebView *)w didFinishNavigation:(WKNavigation *)n { [self pushStatus]; }
- (void)webView:(WKWebView *)w didFailProvisionalNavigation:(WKNavigation *)n withError:(NSError *)e {
    if (e.code == NSURLErrorCancelled) return;
    if ([self.activePage isEqualToString:@"web"]) {
        [self goHome];
        dispatch_async(dispatch_get_main_queue(), ^{ [self showMessage:@"网络连接失败，请检查网络或切换线路"]; });
    }
}
// 相机权限 (iOS 15+): 我们自己的页面直接允许
- (void)webView:(WKWebView *)w requestMediaCapturePermissionForOrigin:(WKSecurityOrigin *)o initiatedByFrame:(WKFrameInfo *)f type:(WKMediaCaptureType)t decisionHandler:(void (^)(WKPermissionDecision))h API_AVAILABLE(ios(15.0)) {
    h(WKPermissionDecisionGrant);
}
- (void)webView:(WKWebView *)w runJavaScriptAlertPanelWithMessage:(NSString *)m initiatedByFrame:(WKFrameInfo *)f completionHandler:(void (^)(void))h {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:nil message:m preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x) { h(); }]];
    [self presentViewController:a animated:YES completion:nil];
}
- (void)webView:(WKWebView *)w runJavaScriptConfirmPanelWithMessage:(NSString *)m initiatedByFrame:(WKFrameInfo *)f completionHandler:(void (^)(BOOL))h {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:nil message:m preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:^(UIAlertAction *x) { h(NO); }]];
    [a addAction:[UIAlertAction actionWithTitle:@"确定" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x) { h(YES); }]];
    [self presentViewController:a animated:YES completion:nil];
}
@end

// ================================================================== App
@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

@implementation AppDelegate
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)opts {
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.rootViewController = [MainVC new];
    [self.window makeKeyAndVisible];
    return YES;
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass(AppDelegate.class));
    }
}
