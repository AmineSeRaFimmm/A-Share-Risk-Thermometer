import { useCallback, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { StatusBar as ExpoStatusBar } from 'expo-status-bar';
import Constants from 'expo-constants';
import { WebView } from 'react-native-webview';

/** Production data plane: GitHub Actions → Pages (no local Mac required). */
const GITHUB_PAGES_URL =
  'https://amineserafimmm.github.io/A-Share-Risk-Thermometer/';

/**
 * Resolve dashboard URL for Expo Go / TestFlight.
 * Priority: EXPO_PUBLIC_WEB_URL → app.config extra.webUrl → GitHub Pages.
 */
function resolveWebUrl(): { url: string; mode: 'pages' | 'custom' } {
  const envUrl = process.env.EXPO_PUBLIC_WEB_URL?.trim();
  if (envUrl) {
    return {
      url: envUrl.endsWith('/') ? envUrl : `${envUrl}/`,
      mode: 'custom',
    };
  }

  const extra = Constants.expoConfig?.extra as { webUrl?: string } | undefined;
  const extraUrl = extra?.webUrl?.trim();
  if (extraUrl) {
    return {
      url: extraUrl.endsWith('/') ? extraUrl : `${extraUrl}/`,
      mode: 'custom',
    };
  }

  return { url: GITHUB_PAGES_URL, mode: 'pages' };
}

const INJECTED_APP_SHELL = `
(function () {
  try {
    document.documentElement.classList.add('in-expo-app');
    document.body && document.body.classList.add('app-shell', 'in-app-webview');
    document.documentElement.style.background = '#f3eee4';
  } catch (e) {}
  true;
})();
`;

export default function App() {
  const webRef = useRef<WebView>(null);
  const resolved = useMemo(() => resolveWebUrl(), []);
  const baseUrl = resolved.url;
  const [uri, setUri] = useState(baseUrl);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setError(null);
    setLoading(true);
    setUri(`${baseUrl}${baseUrl.includes('?') ? '&' : '?'}_t=${Date.now()}`);
  }, [baseUrl]);

  return (
    <SafeAreaView style={styles.safe}>
      <ExpoStatusBar style="dark" />
      <StatusBar barStyle="dark-content" backgroundColor="#f3eee4" />

      <View style={styles.webWrap}>
        {error ? (
          <View style={styles.errorBox}>
            <Text style={styles.errorTitle}>无法连接</Text>
            <Text style={styles.errorBody}>{error}</Text>
            <Text style={styles.errorHint}>{baseUrl}</Text>
            <Pressable onPress={reload} style={styles.retryBtn}>
              <Text style={styles.retryText}>重新加载</Text>
            </Pressable>
          </View>
        ) : (
          <WebView
            ref={webRef}
            source={{ uri }}
            style={styles.webview}
            originWhitelist={['*']}
            allowsBackForwardNavigationGestures={false}
            allowsInlineMediaPlayback
            setSupportMultipleWindows={false}
            javaScriptEnabled
            domStorageEnabled
            sharedCookiesEnabled
            thirdPartyCookiesEnabled
            pullToRefreshEnabled={Platform.OS === 'android'}
            injectedJavaScriptBeforeContentLoaded={INJECTED_APP_SHELL}
            onLoadStart={() => {
              setLoading(true);
              setError(null);
            }}
            onLoadEnd={() => setLoading(false)}
            onError={(e) => {
              setLoading(false);
              setError(e.nativeEvent.description || 'WebView load error');
            }}
            onHttpError={(e) => {
              if (e.nativeEvent.statusCode >= 400) {
                setError(`HTTP ${e.nativeEvent.statusCode}`);
              }
            }}
          />
        )}

        {!error ? (
          <Pressable
            onPress={reload}
            style={styles.refreshFab}
            hitSlop={12}
            accessibilityLabel="刷新"
          >
            <Text style={styles.refreshFabText}>↻</Text>
          </Pressable>
        ) : null}

        {loading && !error ? (
          <View style={styles.loadingOverlay} pointerEvents="none">
            <ActivityIndicator size="large" color="#1a1714" />
          </View>
        ) : null}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: '#f3eee4',
  },
  webWrap: {
    flex: 1,
    backgroundColor: '#f3eee4',
  },
  webview: {
    flex: 1,
    backgroundColor: 'transparent',
  },
  refreshFab: {
    position: 'absolute',
    top: 10,
    right: 12,
    zIndex: 20,
    width: 36,
    height: 36,
    borderWidth: 1,
    borderColor: '#d5ccbc',
    backgroundColor: 'rgba(250, 247, 240, 0.94)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  refreshFabText: {
    fontSize: 18,
    color: '#1a1714',
    fontWeight: '400',
    lineHeight: 20,
  },
  loadingOverlay: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(243, 238, 228, 0.75)',
  },
  errorBox: {
    flex: 1,
    paddingHorizontal: 28,
    justifyContent: 'center',
  },
  errorTitle: {
    fontSize: 22,
    fontWeight: '400',
    color: '#1a1714',
    marginBottom: 12,
  },
  errorBody: {
    fontSize: 14,
    lineHeight: 22,
    color: '#6e655a',
    marginBottom: 12,
  },
  errorHint: {
    fontSize: 11,
    lineHeight: 18,
    color: '#9a9084',
    marginBottom: 24,
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
  },
  retryBtn: {
    alignSelf: 'flex-start',
    paddingHorizontal: 18,
    paddingVertical: 12,
    backgroundColor: '#1a1714',
  },
  retryText: {
    color: '#faf7f0',
    fontWeight: '600',
    letterSpacing: 1,
    fontSize: 12,
  },
});
