import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { EventExpectation, getEvent } from '@/services/api';

// Draft-only editor. Saving requires an admin-authenticated write and the
// mobile app only ever holds the low-privilege read key (see
// docs/event_configuration_storage.md), so this screen never calls the
// versioned admin write endpoint. A future PR must add a real
// mobile-control-auth flow before this form can persist anything.

function listToText(items: string[]): string {
  return items.join('\n');
}

function recordToText(record: Record<string, unknown>): string {
  return Object.entries(record)
    .map(([key, value]) => `${key}: ${value}`)
    .join('\n');
}

export default function EventEditScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const [event, setEvent] = useState<EventExpectation | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [consensusText, setConsensusText] = useState('');
  const [kpiText, setKpiText] = useState('');
  const [bullText, setBullText] = useState('');
  const [baseText, setBaseText] = useState('');
  const [bearText, setBearText] = useState('');
  const [triggersText, setTriggersText] = useState('');
  const [invalidationText, setInvalidationText] = useState('');
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');

  const load = useCallback(async () => {
    if (!eventId) return;
    try {
      setError(null);
      const detail = await getEvent(eventId);
      setEvent(detail);
      setConsensusText(recordToText(detail.consensus));
      setKpiText(listToText(detail.important_kpis));
      setBullText(listToText(detail.bull_case));
      setBaseText(listToText(detail.base_case));
      setBearText(listToText(detail.bear_case));
      setTriggersText(recordToText(detail.triggers));
      setInvalidationText(listToText(detail.invalidation_conditions));
      setSourceName(detail.source_name ?? '');
      setSourceUrl(detail.source_url ?? '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Tuntematon virhe');
    }
  }, [eventId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
    }, 0);
    return () => clearTimeout(timer);
  }, [load]);

  if (!event && !error) {
    return (
      <View style={styles.loadingScreen}>
        <ActivityIndicator color="#8a96a8" />
      </View>
    );
  }

  if (error && !event) {
    return (
      <View style={styles.loadingScreen}>
        <Text style={styles.errorText}>{error}</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Muokkaa seuranta-asetuksia</Text>
      <Text style={styles.subtitle}>
        {event?.event_name} · {event?.instrument}
      </Text>

      <View style={styles.draftNotice}>
        <Text style={styles.draftNoticeTitle}>LUONNOS</Text>
        <Text style={styles.draftNoticeText}>
          Tämä näkymä on UI-luonnos. Tallennus vaatii tulevan turvallisen
          mobile-control-auth-ratkaisun; se ei ole vielä toteutettu, joten
          Tallenna-painike on pois käytöstä eikä lomake kutsu admin-suojattua
          kirjoitusrajapintaa. Read-avainta ei koskaan käytetä
          kirjoitusoperaatioihin.
        </Text>
      </View>

      <Field label="Konsensusmetriikat (avain: arvo per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={consensusText}
          onChangeText={setConsensusText}
          placeholder="fy27_operating_profit_gbp_m: 55.6"
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Tärkeimmät KPI:t (yksi per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={kpiText}
          onChangeText={setKpiText}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Bull-skenaario">
        <TextInput
          style={styles.textArea}
          multiline
          value={bullText}
          onChangeText={setBullText}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Base-skenaario">
        <TextInput
          style={styles.textArea}
          multiline
          value={baseText}
          onChangeText={setBaseText}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Bear-skenaario">
        <TextInput
          style={styles.textArea}
          multiline
          value={bearText}
          onChangeText={setBearText}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Triggerit (positiiviset/negatiiviset, avain: arvo per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={triggersText}
          onChangeText={setTriggersText}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Mitätöintiehdot (yksi per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={invalidationText}
          onChangeText={setInvalidationText}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Lähde">
        <TextInput
          style={styles.textInput}
          value={sourceName}
          onChangeText={setSourceName}
          placeholder="Lähteen nimi"
          placeholderTextColor="#4e5868"
        />
        <TextInput
          style={[styles.textInput, styles.textInputSpacer]}
          value={sourceUrl}
          onChangeText={setSourceUrl}
          placeholder="Lähteen URL"
          placeholderTextColor="#4e5868"
        />
      </Field>

      <View style={styles.saveButton}>
        <Text style={styles.saveButtonText}>Tallenna (tulossa)</Text>
      </View>
    </ScrollView>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: '#0b0e13',
  },
  loadingScreen: {
    flex: 1,
    backgroundColor: '#0b0e13',
    alignItems: 'center',
    justifyContent: 'center',
  },
  content: {
    paddingHorizontal: 18,
    paddingTop: 58,
    paddingBottom: 48,
  },
  title: {
    color: '#f4f7fb',
    fontSize: 22,
    fontWeight: '800',
  },
  subtitle: {
    color: '#8590a1',
    fontSize: 13,
    marginTop: 4,
    marginBottom: 20,
  },
  draftNotice: {
    backgroundColor: '#241d10',
    borderWidth: 1,
    borderColor: '#4a3a1a',
    borderRadius: 14,
    padding: 15,
    marginBottom: 24,
  },
  draftNoticeTitle: {
    color: '#d7ad5f',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.2,
  },
  draftNoticeText: {
    color: '#c7b385',
    fontSize: 13,
    lineHeight: 19,
    marginTop: 6,
  },
  field: {
    marginBottom: 18,
  },
  fieldLabel: {
    color: '#8a96a8',
    fontSize: 12,
    fontWeight: '700',
    marginBottom: 8,
  },
  textArea: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 12,
    padding: 12,
    color: '#e3e8f0',
    minHeight: 80,
    textAlignVertical: 'top',
  },
  textInput: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 12,
    padding: 12,
    color: '#e3e8f0',
  },
  textInputSpacer: {
    marginTop: 8,
  },
  saveButton: {
    backgroundColor: '#1b2230',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    paddingVertical: 15,
    alignItems: 'center',
    marginTop: 8,
  },
  saveButtonText: {
    color: '#626d7e',
    fontSize: 14,
    fontWeight: '700',
  },
  errorText: {
    color: '#e17878',
    fontSize: 13,
  },
});
