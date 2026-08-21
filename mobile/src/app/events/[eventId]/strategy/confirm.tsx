import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import { BackButton } from '@/components/back-button';
import { approveStrategyDraft } from '@/services/api';
import { useStrategyDraft } from './_layout';

// Approval confirmation screen. Two deliberate obstacles stand between
// opening this screen and an actual write happening: an explicit
// "I've checked the limits" acknowledgement switch that gates the approve
// button, and a second native confirmation dialog on top of that button
// press - so a single ordinary tap on a list/card can never approve
// anything by accident.

export default function StrategyConfirmScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const router = useRouter();
  const { draft, preview, setDraft, setPreview } = useStrategyDraft();
  const [approvedBy, setApprovedBy] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  if (!draft || !preview) {
    return (
      <View style={styles.loadingScreen}>
        <BackButton label="Takaisin" />
        <Text style={styles.muted}>
          Esikatselu puuttuu. Palaa yhteenvetoon ja paina &ldquo;Hyväksy strategia&rdquo; uudelleen.
        </Text>
        <Pressable
          style={styles.secondaryButton}
          onPress={() =>
            router.replace({ pathname: '/events/[eventId]/strategy', params: { eventId } })
          }
        >
          <Text style={styles.secondaryButtonText}>Takaisin yhteenvetoon</Text>
        </Pressable>
      </View>
    );
  }

  const nextVersion = preview.base_expectation_version + 1;
  const canSubmit = acknowledged && approvedBy.trim().length > 0 && !submitting;

  const submitApproval = async () => {
    if (!eventId) return;
    setSubmitting(true);
    setError(null);
    try {
      await approveStrategyDraft(eventId, {
        draft: preview.draft,
        draft_fingerprint: preview.draft_fingerprint,
        base_expectation_version: preview.base_expectation_version,
        approved_by: approvedBy.trim(),
        approved_via: 'mobile-app',
      });
      setDraft(null);
      setPreview(null);
      router.replace({ pathname: '/events/[eventId]', params: { eventId } });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Hyväksyntä epäonnistui';
      setError(message);
      // The backend reports both the fingerprint and version CAS checks as
      // 409 Conflict - either way, the previewed draft is stale and must
      // never be resubmitted as-is; the only correct next step is a fresh
      // preview.
      if (message.toLowerCase().includes('conflict') || message.includes('vaihtui') || message.includes('changed since')) {
        setConflict(true);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onApprovePress = () => {
    // Second, distinct confirmation step: a native dialog the user must
    // explicitly act on again, separate from the switch above and the
    // button tap itself.
    Alert.alert(
      'Vahvista hyväksyntä',
      `Strategia lukitaan eventin expectation-versioksi ${nextVersion}. Tätä ei voi perua tämän jälkeen ilman uutta hyväksyttyä versiota.`,
      [
        { text: 'Peruuta', style: 'cancel' },
        { text: 'Vahvista hyväksyntä', style: 'destructive', onPress: () => void submitApproval() },
      ],
    );
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <BackButton label="Takaisin" />

      <Text style={styles.title}>Vahvista hyväksyntä</Text>

      <View style={styles.lockCard}>
        <Text style={styles.lockText}>
          Tämä strategia lukitaan eventin expectation-versioksi {nextVersion}. MarketAI käyttää
          tätä versiota tulosjulkaisun arviointiin.
        </Text>
      </View>

      <Text style={styles.sectionTitle}>NO TRADE / MITÄTÖINTIEHDOT</Text>
      <View style={styles.card}>
        {preview.draft.invalidation_conditions.length === 0 ? (
          <Text style={styles.muted}>Ei mitätöintiehtoja määritelty.</Text>
        ) : (
          preview.draft.invalidation_conditions.map((condition, index) => (
            <Text key={index} style={styles.listItem}>
              • {condition}
            </Text>
          ))
        )}
      </View>

      <Text style={styles.sectionTitle}>TRIGGERIT (RAJAT)</Text>
      <View style={styles.card}>
        {Object.keys(preview.draft.triggers).length === 0 ? (
          <Text style={styles.muted}>Ei triggereitä määritelty.</Text>
        ) : (
          Object.entries(preview.draft.triggers).map(([trigger, value]) => (
            <View key={trigger} style={styles.scoreRow}>
              <Text style={styles.muted}>{trigger}</Text>
              <Text style={styles.score}>{value}</Text>
            </View>
          ))
        )}
      </View>

      {preview.warnings.length > 0 ? (
        <>
          <Text style={styles.sectionTitle}>VAROITUKSET</Text>
          <View style={styles.warningCard}>
            {preview.warnings.map((warning, index) => (
              <Text key={index} style={styles.warningText}>
                ⚠ {warning}
              </Text>
            ))}
          </View>
        </>
      ) : null}

      <Text style={styles.sectionTitle}>HYVÄKSYJÄ</Text>
      <View style={styles.card}>
        <TextInput
          style={styles.textInput}
          value={approvedBy}
          onChangeText={setApprovedBy}
          placeholder="Nimi tai tunniste (vaaditaan audit trailiin)"
          placeholderTextColor="#4e5868"
        />
      </View>

      <Pressable
        style={styles.acknowledgeRow}
        onPress={() => setAcknowledged((value) => !value)}
      >
        <Switch value={acknowledged} onValueChange={setAcknowledged} />
        <Text style={styles.acknowledgeText}>
          Olen tarkistanut rajat ja NO TRADE -ehdot yllä ja hyväksyn version {nextVersion}{' '}
          lukitsemisen.
        </Text>
      </Pressable>

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
          {conflict ? (
            <Pressable
              style={styles.secondaryButton}
              onPress={() => {
                setPreview(null);
                router.replace({ pathname: '/events/[eventId]/strategy', params: { eventId } });
              }}
            >
              <Text style={styles.secondaryButtonText}>Tee esikatselu uudelleen</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      <Pressable
        style={[styles.approveButton, !canSubmit && styles.approveButtonDisabled]}
        disabled={!canSubmit}
        onPress={onApprovePress}
      >
        {submitting ? (
          <ActivityIndicator color="#0b0e13" />
        ) : (
          <Text style={styles.approveButtonText}>Hyväksy ja lukitse</Text>
        )}
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0b0e13' },
  loadingScreen: {
    flex: 1,
    backgroundColor: '#0b0e13',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
    gap: 16,
  },
  content: { paddingHorizontal: 18, paddingTop: 58, paddingBottom: 48 },
  title: { color: '#f4f7fb', fontSize: 22, fontWeight: '800', marginBottom: 16 },
  lockCard: {
    backgroundColor: '#17233a',
    borderWidth: 1,
    borderColor: '#2a3c5e',
    borderRadius: 16,
    padding: 16,
    marginBottom: 20,
  },
  lockText: { color: '#cfe0fb', fontSize: 14, lineHeight: 21, fontWeight: '600' },
  sectionTitle: {
    color: '#687386',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.4,
    marginBottom: 8,
    marginLeft: 3,
  },
  card: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 16,
    marginBottom: 14,
  },
  listItem: { color: '#b7c2d7', fontSize: 13, lineHeight: 20 },
  muted: { color: '#6f7a8b', fontSize: 12, lineHeight: 18 },
  scoreRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4 },
  score: { color: '#c7ced8', fontWeight: '700' },
  warningCard: {
    backgroundColor: '#241d10',
    borderWidth: 1,
    borderColor: '#4a3a1a',
    borderRadius: 16,
    padding: 16,
    marginBottom: 14,
    gap: 6,
  },
  warningText: { color: '#c7b385', fontSize: 13, lineHeight: 19 },
  textInput: {
    backgroundColor: '#0f141c',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 10,
    padding: 12,
    color: '#e3e8f0',
  },
  acknowledgeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 16,
    marginTop: 8,
    marginBottom: 16,
  },
  acknowledgeText: { color: '#d9dee7', fontSize: 13, lineHeight: 19, flex: 1 },
  errorBox: { marginBottom: 16, gap: 10 },
  errorText: { color: '#e17878', fontSize: 13 },
  secondaryButton: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    paddingVertical: 13,
    alignItems: 'center',
  },
  secondaryButtonText: { color: '#72b8db', fontSize: 14, fontWeight: '700' },
  approveButton: {
    backgroundColor: '#4fae7a',
    borderRadius: 14,
    paddingVertical: 16,
    alignItems: 'center',
    marginBottom: 40,
  },
  approveButtonDisabled: { opacity: 0.4 },
  approveButtonText: { color: '#0b0e13', fontSize: 15, fontWeight: '800' },
});
