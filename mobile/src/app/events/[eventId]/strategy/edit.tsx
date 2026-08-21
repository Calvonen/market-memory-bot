import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { BackButton } from '@/components/back-button';
import { DraftFormState, parseTypedRecord } from '@/utils/strategy-draft-format';
import { useResetOnKeyChange } from '@/utils/use-reset-on-key-change';
import { useStrategyDraft } from './_layout';

// Structured draft editor: produces a local DraftFormState (never written
// to the backend directly - "Tallenna luonnos" only updates the shared
// context). Persisting anything server-side always goes through
// preview -> confirm -> approve, never from this screen.

export default function StrategyDraftEditScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const router = useRouter();
  const { draft, setDraft, setPreview, event } = useStrategyDraft();
  // Lazy-initialized once from the shared context draft: edits here stay
  // local until "Tallenna luonnos" writes them back, so navigating away
  // without saving discards them instead of mutating the shared draft.
  const [local, setLocal] = useState<DraftFormState | null>(() => draft);

  // If eventId changes while this screen stays mounted, re-sync `local`
  // from the (now layout-reset) context draft immediately - never keep
  // showing a previous event's edits under the new event's route.
  useResetOnKeyChange(eventId, () => setLocal(draft));

  if (!draft || !local || !event) {
    return (
      <View style={styles.loadingScreen}>
        <BackButton label="Takaisin" />
        <Text style={styles.muted}>Luonnosta ei löytynyt. Palaa yhteenvetoon.</Text>
      </View>
    );
  }

  const update = (patch: Partial<DraftFormState>) => setLocal((prev) => (prev ? { ...prev, ...patch } : prev));

  const save = () => {
    setDraft(local);
    // The just-saved draft may differ from whatever was last previewed -
    // an existing preview's ESIKATSELTU state and warnings must never
    // keep being shown (or be approvable) against a draft that has since
    // changed. Clear it unconditionally on every save, not just when the
    // edited fields obviously changed, so the user is always forced
    // through a fresh preview before they can approve again.
    setPreview(null);
    router.back();
  };

  // Consensus/triggers are edited as raw JSON object text - shown here so
  // an invalid value is visible immediately, not just when the user later
  // tries to preview/approve. Never blocks saving the draft locally
  // (that's harmless client-side state); only preview/approve actually
  // enforces this (draftFormToInput throws for the same reason).
  const consensusValidation = parseTypedRecord(local.consensusText);
  const triggersValidation = parseTypedRecord(local.triggersText);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <BackButton label="Takaisin" />

      <Text style={styles.title}>Muokkaa luonnosta</Text>
      <Text style={styles.subtitle}>
        {event.event_name} · {event.instrument}
      </Text>

      <Field label="Yhteenveto (ihmisluettava kuvaus strategiasta)">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.summary}
          onChangeText={(value) => update({ summary: value })}
          placeholder="Lyhyt yhteenveto strategiasta ja sen perusteista"
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Konsensusmetriikat (JSON-objekti)">
        <TextInput
          style={styles.jsonArea}
          multiline
          autoCapitalize="none"
          autoCorrect={false}
          value={local.consensusText}
          onChangeText={(value) => update({ consensusText: value })}
          placeholder='{"fy27_operating_profit_pre_exceptional_gbp_m": 55.6}'
          placeholderTextColor="#4e5868"
        />
        {!consensusValidation.ok ? (
          <Text style={styles.fieldError}>{consensusValidation.error}</Text>
        ) : null}
      </Field>

      <Field label="Tärkeimmät KPI:t (yksi per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.kpiText}
          onChangeText={(value) => update({ kpiText: value })}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Bull-skenaario">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.bullText}
          onChangeText={(value) => update({ bullText: value })}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Base-skenaario">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.baseText}
          onChangeText={(value) => update({ baseText: value })}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Bear-skenaario">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.bearText}
          onChangeText={(value) => update({ bearText: value })}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Triggerit (JSON-objekti, avaimet bull_/bear_ + metriikka)">
        <TextInput
          style={styles.jsonArea}
          multiline
          autoCapitalize="none"
          autoCorrect={false}
          value={local.triggersText}
          onChangeText={(value) => update({ triggersText: value })}
          placeholder='{"bull_fy27_operating_profit_gbp_m": 62.0}'
          placeholderTextColor="#4e5868"
        />
        {!triggersValidation.ok ? (
          <Text style={styles.fieldError}>{triggersValidation.error}</Text>
        ) : null}
      </Field>

      <Field label="NO TRADE / mitätöintiehdot (yksi per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.invalidationText}
          onChangeText={(value) => update({ invalidationText: value })}
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Lähde">
        <TextInput
          style={styles.textInput}
          value={local.sourceName}
          onChangeText={(value) => update({ sourceName: value })}
          placeholder="Lähteen nimi"
          placeholderTextColor="#4e5868"
        />
        <TextInput
          style={[styles.textInput, styles.textInputSpacer]}
          value={local.sourceUrl}
          onChangeText={(value) => update({ sourceUrl: value })}
          placeholder="Lähteen URL"
          placeholderTextColor="#4e5868"
        />
        <TextInput
          style={[styles.textInput, styles.textInputSpacer]}
          value={local.sourceAsOf}
          onChangeText={(value) => update({ sourceAsOf: value })}
          placeholder="Päivätty (VVVV-KK-PP)"
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Oletukset (yksi per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.assumptionsText}
          onChangeText={(value) => update({ assumptionsText: value })}
          placeholder="Mihin strategia perustuu"
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Avoimet kysymykset (yksi per rivi)">
        <TextInput
          style={styles.textArea}
          multiline
          value={local.unresolvedText}
          onChangeText={(value) => update({ unresolvedText: value })}
          placeholder="Mitä pitää vielä selvittää ennen hyväksyntää"
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Field label="Muutoksen kuvaus (change_note, vaaditaan hyväksynnässä)">
        <TextInput
          style={styles.textInput}
          value={local.changeNote}
          onChangeText={(value) => update({ changeNote: value })}
          placeholder="Miksi tämä versio ehdotetaan"
          placeholderTextColor="#4e5868"
        />
      </Field>

      <Pressable style={styles.saveButton} onPress={save}>
        <Text style={styles.saveButtonText}>Tallenna luonnos</Text>
      </Pressable>
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
  screen: { flex: 1, backgroundColor: '#0b0e13' },
  loadingScreen: {
    flex: 1,
    backgroundColor: '#0b0e13',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  content: { paddingHorizontal: 18, paddingTop: 58, paddingBottom: 48 },
  title: { color: '#f4f7fb', fontSize: 22, fontWeight: '800' },
  subtitle: { color: '#8590a1', fontSize: 13, marginTop: 4, marginBottom: 20 },
  field: { marginBottom: 18 },
  fieldLabel: { color: '#8a96a8', fontSize: 12, fontWeight: '700', marginBottom: 8 },
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
  jsonArea: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 12,
    padding: 12,
    color: '#e3e8f0',
    minHeight: 120,
    textAlignVertical: 'top',
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
    fontSize: 13,
  },
  fieldError: { color: '#e17878', fontSize: 12, lineHeight: 17, marginTop: 6 },
  textInput: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 12,
    padding: 12,
    color: '#e3e8f0',
  },
  textInputSpacer: { marginTop: 8 },
  saveButton: {
    backgroundColor: '#208AEF',
    borderRadius: 14,
    paddingVertical: 15,
    alignItems: 'center',
    marginTop: 8,
    marginBottom: 40,
  },
  saveButtonText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  muted: { color: '#6f7a8b', fontSize: 13 },
});
