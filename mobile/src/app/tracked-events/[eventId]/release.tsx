import { useLocalSearchParams } from 'expo-router';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { BackButton } from '@/components/back-button';

export default function TrackedEventReleaseHandoffScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <BackButton />
      <Text style={styles.eyebrow}>WORKFLOW · JULKAISU</Text>
      <Text style={styles.title}>Julkaisun tarkistus</Text>
      <Text style={styles.body}>
        Tämä näkymä kuuluu canonical tracked-event -workflow’hun. Seuraavassa vaiheessa tähän
        kytketään julkaisun URL/PDF-handoff samaan backendin release-putkeen.
      </Text>

      <View style={styles.card}>
        <Text style={styles.label}>Tracked event</Text>
        <Text style={styles.value} selectable>
          {eventId || 'Tuntematon tapahtuma'}
        </Text>
      </View>

      <Text style={styles.note}>
        Tässä vaiheessa näkymä on tarkoituksella read-only: se ei käynnistä ingestionia, muuta
        workflow-tilaa eikä luo trading taskia.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    backgroundColor: '#0d1118',
    padding: 20,
    paddingTop: 56,
  },
  eyebrow: {
    color: '#8590a1',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
    marginTop: 24,
  },
  title: {
    color: '#f4f7fb',
    fontSize: 24,
    fontWeight: '800',
    marginTop: 6,
  },
  body: {
    color: '#aab3c2',
    fontSize: 14,
    lineHeight: 21,
    marginTop: 12,
  },
  card: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    padding: 14,
    marginTop: 20,
  },
  label: {
    color: '#8590a1',
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  value: {
    color: '#d8dee8',
    fontSize: 13,
    marginTop: 5,
  },
  note: {
    color: '#8994a6',
    fontSize: 12,
    lineHeight: 18,
    marginTop: 16,
  },
});
