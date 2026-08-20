import { PropsWithChildren } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

export function ScreenShell({ title, subtitle, children }: PropsWithChildren<{ title: string; subtitle: string }>) {
  return <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
    <Text style={styles.title}>{title}</Text><Text style={styles.subtitle}>{subtitle}</Text>
    <View style={styles.body}>{children}</View>
  </ScrollView>;
}
export const shared = StyleSheet.create({
  card: { backgroundColor: '#172033', borderRadius: 14, padding: 16, gap: 8 },
  heading: { color: '#fff', fontWeight: '700', fontSize: 17 }, text: { color: '#b7c2d7' },
  button: { backgroundColor: '#208AEF', borderRadius: 10, padding: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' }, input: { backgroundColor: '#fff', borderRadius: 10, padding: 14 },
});
const styles = StyleSheet.create({ screen: { flex: 1, backgroundColor: '#0b1020' }, content: { padding: 20, paddingTop: 64, paddingBottom: 110 }, title: { color: '#fff', fontSize: 28, fontWeight: '800' }, subtitle: { color: '#8f9bb2', marginTop: 4 }, body: { gap: 14, marginTop: 24 } });
