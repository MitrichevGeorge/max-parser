## Список opcodes из java:

```java
package defpackage;

import com.vk.push.core.base.AidlException;
import java.util.HashMap;

/* JADX INFO: loaded from: classes.dex */
public final class e8c {
    public static final e8c J3;
    public static final e8c K3;
    public static final e8c L3;
    public static final e8c M3;
    public static final e8c N3;
    public static final e8c O3;
    public static final e8c P3;
    public static final e8c Q3;
    public static final e8c R3;
    public static final e8c S3;
    public static final e8c T3;
    public static final e8c U3;
    public static final e8c V3;
    public static final e8c W3;
    public static final e8c X3;
    public static final /* synthetic */ e8c[] Y3;
    public static final /* synthetic */ u56 Z3;
    public static final dab c;
    public static final HashMap d;
    public static final HashMap e;
    public final short a;
    public final br3 b;
    public static final e8c f = new e8c("PING", 0, 1, null);
    public static final e8c g = new e8c("DEBUG", 1, 2, null);
    public static final e8c h = new e8c("RECONNECT", 2, 3, null);
    public static final e8c i = new e8c("LOG", 3, 5, null);
    public static final e8c j = new e8c("SESSION_INIT", 4, 6, null);
    public static final e8c k = new e8c("PROFILE", 5, 16, null);
    public static final e8c l = new e8c("AUTH_REQUEST", 6, 17, null);
    public static final e8c m = new e8c("AUTH", 7, 18, null);
    public static final e8c n = new e8c("LOGIN", 8, 19, ayf.k);
    public static final e8c o = new e8c("LOGIN2", 9, 8, coc.i);
    public static final e8c p = new e8c("LOGOUT", 10, 20, null);
    public static final e8c q = new e8c("SYNC", 11, 21, null);
    public static final e8c r = new e8c("CONFIG", 12, 22, coc.f);
    public static final e8c s = new e8c("AUTH_CONFIRM", 13, 23, null);
    public static final e8c t = new e8c("AUTH_CREATE_TRACK", 14, 112, gu5.b);
    public static final e8c u = new e8c("AUTH_CHECK_PASSWORD", 15, 113, s45.c);
    public static final e8c v = new e8c("AUTH_LOGIN_CHECK_PASSWORD", 16, 115, y9b.c);
    public static final e8c w = new e8c("AUTH_LOGIN_PROFILE_DELETE", 17, 116, cab.c);
    public static final e8c x = new e8c("AUTH_LOGIN_RESTORE_PASSWORD", 18, 101, dab.c);
    public static final e8c y = new e8c("AUTH_VALIDATE_PASSWORD", 19, 107, xd0.c);
    public static final e8c z = new e8c("AUTH_VALIDATE_HINT", 20, 108, xd0.b);
    public static final e8c A = new e8c("AUTH_VERIFY_EMAIL", 21, 109, fab.c);
    public static final e8c B = new e8c("AUTH_CHECK_EMAIL", 22, 110, ayf.d);
    public static final e8c C = new e8c("AUTH_SET_2FA", 23, 111, xd0.a);
    public static final e8c D = new e8c("AUTH_2FA_DETAILS", 24, 104, coc.c);
    public static final e8c E = new e8c("ASSETS_GET", 25, 26, null);
    public static final e8c F = new e8c("ASSETS_UPDATE", 26, 27, null);
    public static final e8c G = new e8c("ASSETS_GET_BY_IDS", 27, 28, null);
    public static final e8c H = new e8c("ASSETS_LIST_MODIFY", 28, 261, null);
    public static final e8c I = new e8c("ASSETS_REMOVE", 29, 259, null);
    public static final e8c J = new e8c("ASSETS_MOVE", 30, 260, null);
    public static final e8c K = new e8c("ASSETS_ADD", 31, 29, null);
    public static final e8c X = new e8c("PRESET_AVATARS", 32, 25, coc.l);
    public static final e8c Y = new e8c("CONTACT_INFO", 33, 32, gu5.d);
    public static final e8c Z = new e8c("CONTACT_INFO_BY_PHONE", 34, 46, s45.f);
    public static final e8c n1 = new e8c("CONTACT_ADD", 35, 33, null);
    public static final e8c o1 = new e8c("CONTACT_UPDATE", 36, 34, null);
    public static final e8c p1 = new e8c("CONTACT_PRESENCE", 37, 35, y9b.f);
    public static final e8c q1 = new e8c("CONTACT_LIST", 38, 36, null);
    public static final e8c r1 = new e8c("CONTACT_SEARCH", 39, 37, null);
    public static final e8c s1 = new e8c("CONTACT_PHOTOS", 40, 39, null);
    public static final e8c t1 = new e8c("CONTACT_SORT", 41, 40, null);
    public static final e8c u1 = new e8c("CONTACT_VERIFY", 42, 42, null);
    public static final e8c v1 = new e8c("REMOVE_CONTACT_PHOTO", 43, 43, null);
    public static final e8c w1 = new e8c("CHAT_INFO", 44, 48, null);
    public static final e8c x1 = new e8c("CHAT_HISTORY", 45, 49, null);
    public static final e8c y1 = new e8c("CHAT_MARK", 46, 50, lp6.e);
    public static final e8c z1 = new e8c("CHAT_MEDIA", 47, 51, null);
    public static final e8c A1 = new e8c("CHAT_DELETE", 48, 52, null);
    public static final e8c B1 = new e8c("CHATS_LIST", 49, 53, null);
    public static final e8c C1 = new e8c("CHAT_CLEAR", 50, 54, null);
    public static final e8c D1 = new e8c("CHAT_UPDATE", 51, 55, null);
    public static final e8c E1 = new e8c("CHAT_CHECK_LINK", 52, 56, null);
    public static final e8c F1 = new e8c("CHAT_JOIN", 53, 57, fab.d);
    public static final e8c G1 = new e8c("CHAT_LEAVE", 54, 58, null);
    public static final e8c H1 = new e8c("CHAT_MEMBERS", 55, 59, coc.e);
    public static final e8c I1 = new e8c("PUBLIC_SEARCH", 56, 60, null);
    public static final e8c J1 = new e8c("CHAT_PERSONAL_CONFIG", 57, 61, ayf.f);
    public static final e8c K1 = new e8c("REACTIONS_SETTINGS_GET_BY_CHAT_ID", 58, 258, dab.d);
    public static final e8c L1 = new e8c("CHAT_REACTIONS_SETTINGS_SET", 59, 257, s45.e);
    public static final e8c M1 = new e8c("CHAT_CHECK_ESIA", 60, 307, xd0.d);
    public static final e8c N1 = new e8c("MSG_SEND", 61, 64);
    public static final e8c O1 = new e8c("MSG_TYPING", 62, 65);
    public static final e8c P1 = new e8c("MSG_DELETE", 63, 66);
    public static final e8c Q1 = new e8c("MSG_EDIT", 64, 67);
    public static final e8c R1 = new e8c("GET_COMMENTS_UPDATES", 65, 91, y9b.h);
    public static final e8c S1 = new e8c("MSG_DELETE_RANGE", 66, 92);
    public static final e8c T1 = new e8c("MSG_REACTION", 67, 178, lo0.k);
    public static final e8c U1 = new e8c("MSG_CANCEL_REACTION", 68, 179, gu5.g);
    public static final e8c V1 = new e8c("MSG_GET_REACTIONS", 69, 180, fab.i);
    public static final e8c W1 = new e8c("MSG_GET_DETAILED_REACTIONS", 70, 181);
    public static final e8c X1 = new e8c("STORIES_LIST", 71, 208, fab.l);
    public static final e8c Y1 = new e8c("STORIES_LIST_BY_OWNER_ID", 72, 209, dab.m);
    public static final e8c Z1 = new e8c("STORIES_GET_BY_OWNER_ID", 73, 210, s45.m);
    public static final e8c a2 = new e8c("STORIES_GET_STATS", 74, 211, cab.l);
    public static final e8c b2 = new e8c("STORIES_GET_DETAILED_STATS", 75, 212, y9b.l);
    public static final e8c c2 = new e8c("STORIES_REACT", 76, 213, lp6.o);
    public static final e8c d2 = new e8c("STORIES_MARK", 77, 214, lo0.o);
    public static final e8c e2 = new e8c("STORIES_SEND", 78, 215, coc.n);
    public static final e8c f2 = new e8c("NOTIF_STORIES_UPDATE", 79, 216, s45.k);
    public static final e8c g2 = new e8c("STORIES_EDIT", 80, 217, ayf.n);
    public static final e8c h2 = new e8c("STORIES_DELETE", 81, 218, coc.m);
    public static final e8c i2 = new e8c("STORIES_GET_BY_STORY_ID", 82, 220, gu5.k);
    public static final e8c j2 = new e8c("CHAT_SEARCH", 83, 68);
    public static final e8c k2 = new e8c("MSG_SHARE_PREVIEW", 84, 70);
    public static final e8c l2 = new e8c("MSG_GET", 85, 71, cab.h);
    public static final e8c m2 = new e8c("MSG_SEARCH_TOUCH", 86, 72);
    public static final e8c n2 = new e8c("MSG_SEARCH", 87, 73);
    public static final e8c o2 = new e8c("MSG_GET_STAT", 88, 74);
    public static final e8c p2 = new e8c("CHAT_SUBSCRIBE", 89, 75);
    public static final e8c q2 = new e8c("VIDEO_CHAT_START", 90, 76, y9b.d);
    public static final e8c r2 = new e8c("VIDEO_CHAT_START_ACTIVE", 91, 78, gu5.m);
    public static final e8c s2 = new e8c("VIDEO_CHAT_JOIN", 92, 166, s45.o);
    public static final e8c t2 = new e8c("VIDEO_CHAT_HANGUP", 93, 167, ayf.p);
    public static final e8c u2 = new e8c("CHAT_MEMBERS_UPDATE", 94, 77);
    public static final e8c v2 = new e8c("VIDEO_CHAT_HISTORY", 95, 79);
    public static final e8c w2 = new e8c("PHOTO_UPLOAD", 96, 80, coc.b);
    public static final e8c x2 = new e8c("STICKER_UPLOAD", 97, 81);
    public static final e8c y2 = new e8c("VIDEO_UPLOAD", 98, 82, y9b.n);
    public static final e8c z2 = new e8c("VIDEO_PLAY", 99, 83);
    public static final e8c A2 = new e8c("VIDEO_CHAT_CREATE_JOIN_LINK", 100, 84, coc.d);
    public static final e8c B2 = new e8c("CHAT_PIN_SET_VISIBILITY", 101, 86);
    public static final e8c C2 = new e8c("FILE_UPLOAD", 102, 87);
    public static final e8c D2 = new e8c("FILE_DOWNLOAD", AidlException.HOST_IS_NOT_MASTER, 88, fab.g);
    public static final e8c E2 = new e8c("LINK_INFO", AidlException.SDK_IS_NOT_INITIALIZED, 89, lo0.j);
    public static final e8c F2 = new e8c("SESSIONS_INFO", AidlException.TRANSFERRED_IPC_DATA_EXCEPTION, 96);
    public static final e8c G2 = new e8c("SESSIONS_CLOSE", 106, 97);
    public static final e8c H2 = new e8c("PHONE_BIND_REQUEST", 107, 98);
    public static final e8c I2 = new e8c("PHONE_BIND_CONFIRM", 108, 99);
    public static final e8c J2 = new e8c("GET_INBOUND_CALLS", 109, 103);
    public static final e8c K2 = new e8c("EXTERNAL_CALLBACK", 110, 105, dab.g);
    public static final e8c L2 = new e8c("PHONE_WEBAPP_SHARE", 111, 106, lo0.q);
    public static final e8c M2 = new e8c("OK_TOKEN", 112, 158, gu5.i);
    public static final e8c N2 = new e8c("CHAT_COMPLAIN", 113, 117);
    public static final e8c O2 = new e8c("MSG_SEND_CALLBACK", 114, 118);
    public static final e8c P2 = new e8c("SUSPEND_BOT", 115, 119);
    public static final e8c Q2 = new e8c("LOCATION_STOP", 116, 124);
    public static final e8c R2 = new e8c("LOCATION_SEND", 117, 125);
    public static final e8c S2 = new e8c("LOCATION_REQUEST", 118, 126);
    public static final e8c T2 = new e8c("GET_LAST_MENTIONS", 119, 127);
    public static final e8c U2 = new e8c("STICKER_CREATE", 120, 193);
    public static final e8c V2 = new e8c("STICKER_SUGGEST", 121, 194);
    public static final e8c W2 = new e8c("VIDEO_CHAT_MEMBERS", 122, 195);
    public static final e8c X2 = new e8c("NOTIF_MESSAGE", 123, 128, tcb.a);
    public static final e8c Y2 = new e8c("NOTIF_TYPING", 124, 129);
    public static final e8c Z2 = new e8c("NOTIF_MARK", 125, 130, lo0.l);
    public static final e8c a3 = new e8c("NOTIF_CONTACT", 126, 131);
    public static final e8c b3 = new e8c("NOTIF_PRESENCE", 127, 132);
    public static final e8c c3 = new e8c("NOTIF_CONFIG", so0.m, 134);
    public static final e8c d3 = new e8c("NOTIF_CHAT", 129, 135);
    public static final e8c e3 = new e8c("NOTIF_ATTACH", 130, 136, y9b.j);
    public static final e8c f3 = new e8c("NOTIF_CALL_START", 131, 137);
    public static final e8c g3 = new e8c("NOTIF_CONTACT_SORT", 132, 139);
    public static final e8c h3 = new e8c("NOTIF_MSG_DELETE_RANGE", 133, 140);
    public static final e8c i3 = new e8c("NOTIF_MSG_DELETE", 134, 142, coc.k);
    public static final e8c j3 = new e8c("NOTIF_MSG_REACTIONS_CHANGED", 135, 155);
    public static final e8c k3 = new e8c("NOTIF_MSG_YOU_REACTED", 136, 156);
    public static final e8c l3 = new e8c("NOTIF_CALLBACK_ANSWER", 137, 143);
    public static final e8c m3 = new e8c("CHAT_BOT_COMMANDS", 138, 144);
    public static final e8c n3 = new e8c("BOT_INFO", 139, 145, lp6.d);
    public static final e8c o3 = new e8c("NOTIF_LOCATION", 140, 147);
    public static final e8c p3 = new e8c("NOTIF_LOCATION_REQUEST", 141, 148);
    public static final e8c q3 = new e8c("NOTIF_ASSETS_UPDATE", 142, 150);
    public static final e8c r3 = new e8c("CHAT_HIDE", 143, 196);
    public static final e8c s3 = new e8c("CHAT_SEARCH_COMMON_PARTICIPANTS", 144, 198);
    public static final e8c t3 = new e8c("NOTIF_MSG_DELAYED", 145, 154, lp6.k);
    public static final e8c u3 = new e8c("NOTIF_PROFILE", 146, 159, ayf.m);
    public static final e8c v3 = new e8c("PROFILE_DELETE", 147, 199, s45.l);
    public static final e8c w3 = new e8c("PROFILE_DELETE_TIME", 148, 200, gu5.j);
    public static final e8c x3 = new e8c("WEB_APP_INIT_DATA", 149, 160, dab.o);
    public static final e8c y3 = new e8c("COMPLAIN", 150, 161, y9b.e);
    public static final e8c z3 = new e8c("COMPLAIN_REASONS_GET", 151, 162, cab.e);
    public static final e8c A3 = new e8c("CALL_HISTORY", 152, 163, s45.d);
    public static final e8c B3 = new e8c("CALL_HISTORY_CLEAR", 153, 164, ayf.e);
    public static final e8c C3 = new e8c("NOTIF_CALL_HISTORY", 154, 165, dab.j);
    public static final e8c D3 = new e8c("FOLDERS_GET", 155, 272, coc.h);
    public static final e8c E3 = new e8c("FOLDERS_GET_BY_ID", 156, 273, lp6.h);
    public static final e8c F3 = new e8c("FOLDERS_UPDATE", 157, 274, s45.h);
    public static final e8c G3 = new e8c("FOLDERS_REORDER", 158, 275, ayf.j);
    public static final e8c H3 = new e8c("FOLDERS_DELETE", 159, 276, lo0.i);
    public static final e8c I3 = new e8c("NOTIF_FOLDERS", 160, 277, fab.j);

    static {
        cab cabVar = cab.i;
        J3 = new e8c("AUTH_QR_APPROVE", 161, (short) 290, cabVar);
        K3 = new e8c("NOTIF_BANNERS", 162, (short) 292, cabVar);
        L3 = new e8c("BANNERS_GET", 163, (short) 302, lo0.b);
        M3 = new e8c("CHAT_SUGGEST", 164, (short) 300, gu5.c);
        N3 = new e8c("AUDIO_PLAY", 165, (short) 301, lp6.c);
        O3 = new e8c("MSG_DELIVERY", 166, (short) 303, xd0.e);
        P3 = new e8c("SEND_VOTE", 167, (short) 304, lo0.m);
        Q3 = new e8c("VOTERS_LIST_BY_ANSWER", 168, (short) 305, lp6.l);
        R3 = new e8c("GET_POLL_UPDATES", 169, (short) 306, dab.i);
        S3 = new e8c("TRANSCRIBE_MEDIA", 170, (short) 202, ioh.a);
        T3 = new e8c("NOTIF_TRANSCRIPTION", 171, (short) 293, mdb.a);
        U3 = new e8c("ORG_INFO", 172, (short) 256, cab.j);
        V3 = new e8c("CHAT_LIVESTREAM_INFO", 173, (short) 62, lo0.e);
        W3 = new e8c("PHOTO_URL_REFRESH", 174, (short) 203, dab.k);
        X3 = new e8c("MSG_DELETE_USER", 175, (short) 94, y9b.i);
        e8c[] e8cVarArr = {f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, w, x, y, z, A, B, C, D, E, F, G, H, I, J, K, X, Y, Z, n1, o1, p1, q1, r1, s1, t1, u1, v1, w1, x1, y1, z1, A1, B1, C1, D1, E1, F1, G1, H1, I1, J1, K1, L1, M1, N1, O1, P1, Q1, R1, S1, T1, U1, V1, W1, X1, Y1, Z1, a2, b2, c2, d2, e2, f2, g2, h2, i2, j2, k2, l2, m2, n2, o2, p2, q2, r2, s2, t2, u2, v2, w2, x2, y2, z2, A2, B2, C2, D2, E2, F2, G2, H2, I2, J2, K2, L2, M2, N2, O2, P2, Q2, R2, S2, T2, U2, V2, W2, X2, Y2, Z2, a3, b3, c3, d3, e3, f3, g3, h3, i3, j3, k3, l3, m3, n3, o3, p3, q3, r3, s3, t3, u3, v3, w3, x3, y3, z3, A3, B3, C3, D3, E3, F3, G3, H3, I3, J3, K3, L3, M3, N3, O3, P3, Q3, R3, S3, T3, U3, V3, W3, X3};
        Y3 = e8cVarArr;
        Z3 = sl0.n(e8cVarArr);
        c = new dab(21);
        d = new HashMap();
        e = new HashMap();
        for (e8c e8cVar : Z3) {
            d.put(Short.valueOf(e8cVar.a), e8cVar.name());
            HashMap map = e;
            Short shValueOf = Short.valueOf(e8cVar.a);
            String strName = e8cVar.name();
            dab dabVar = c;
            short s4 = e8cVar.a;
            dabVar.getClass();
            map.put(shValueOf, nzg.u(strName, "(0x", Integer.toHexString(s4 & 65535), ")"));
        }
    }

    public e8c(String str, int i4, short s4, br3 br3Var) {
        super(str, i4);
        this.a = s4;
        this.b = br3Var;
    }

    public static e8c valueOf(String str) {
        return (e8c) Enum.valueOf(e8c.class, str);
    }

    public static e8c[] values() {
        return (e8c[]) Y3.clone();
    }

    public /* synthetic */ e8c(String str, int i4, short s4) {
        this(str, i4, s4, null);
    }
}

```

