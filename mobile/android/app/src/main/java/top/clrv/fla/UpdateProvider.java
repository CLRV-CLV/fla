package top.clrv.fla;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/** 把下载好的新版 APK 以 content:// 交给系统安装器 (Android 7+ 不允许 file://; 不依赖 androidx FileProvider) */
public class UpdateProvider extends ContentProvider {
    static final String AUTH = "top.clrv.fla.update";

    static File apkFile(android.content.Context c) { return new File(c.getCacheDir(), "update.apk"); }

    @Override public boolean onCreate() { return true; }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = apkFile(getContext());
        if (!f.exists()) throw new FileNotFoundException();
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public String getType(Uri uri) { return "application/vnd.android.package-archive"; }

    @Override
    public Cursor query(Uri uri, String[] p, String s, String[] a, String o) {
        File f = apkFile(getContext());
        MatrixCursor c = new MatrixCursor(new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        c.addRow(new Object[]{"FLA-update.apk", f.length()});
        return c;
    }

    @Override public Uri insert(Uri uri, ContentValues v) { return null; }
    @Override public int delete(Uri uri, String s, String[] a) { return 0; }
    @Override public int update(Uri uri, ContentValues v, String s, String[] a) { return 0; }
}
