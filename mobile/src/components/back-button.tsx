import { router } from 'expo-router';
import { Pressable, StyleSheet, Text } from 'react-native';

// The root Stack renders every events/* route without a header (see
// app/_layout.tsx), so a screen opened without in-app history - a deep
// link, a shared URL, a browser reload on web - would otherwise be a dead
// end with no way back to the tabs. router.canGoBack() tells us whether
// there is a previous screen to pop to; when there isn't, replace with the
// home tab instead of pushing (so this screen doesn't stay in the stack
// underneath it).
function goBack() {
  if (router.canGoBack()) {
    router.back();
  } else {
    router.replace('/');
  }
}

export function BackButton({ label = 'Takaisin' }: { label?: string }) {
  return (
    <Pressable style={styles.button} onPress={goBack} hitSlop={8}>
      <Text style={styles.text}>← {label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    alignSelf: 'flex-start',
    paddingVertical: 6,
    marginBottom: 14,
  },
  text: {
    color: '#72b8db',
    fontSize: 14,
    fontWeight: '700',
  },
});
