import { useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { BackButton } from '@/components/back-button';
import {
  getTrackedEventReleaseSource,
  getTrackedEventWorkflow,
  ingestTrackedEventRelease,
  putTrackedEventReleaseSource,
  type TrackedEventReleaseSource,
  type TrackedEventWorkflowResponse,
} from '@/services/tracked-events';

export default function TrackedEventReleaseHandoffScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const [releaseSource, setReleaseSource] = useState<TrackedEventReleaseSource | null>(null);
  const [workflow, setWorkflow] = useState<TrackedEventWorkflowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sourceUrl, setSourceUrl] = useState('');
  const [sourceTitle, setSourceTitle] = useState('');
  const [actor, setActor] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [processMessage, setProcessMessage] = useState<string | null>(null);
  const eventIdRef = useRef(eventId);
  const mountedRef = useRef(true);

  const releaseStep = workflow?.steps.find((step) => step.key === 'release') ?? null;
  const canProcessRelease = Boolean(
    releaseSource?.active
      && releaseStep?.status === 'action_required'
      && releaseStep.action_target === 'release',
  );

  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  useEffect(() => {
    eventIdRef.current = eventId;
  }, [eventId]);

  useEffect(() => {
    let cancelled = false;
    // Reset stale state immediately when the route switches to another event.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setReleaseSource(null);
    setWorkflow(null);
    setError(null);
    setSourceUrl('');
    setSourceTitle('');
    setActor('');
    setSubmitting(false);
    setProcessing(false);
    setSubmitMessage(null);
    setProcessMessage(null);

    async function loadReleaseState() {
      if (!eventId) {
        setError('Tracked event puuttuu.');
        setLoading(false);
        return;
      }

      try {
        const [source, currentWorkflow] = await Promise.all([
          getTrackedEventReleaseSource(eventId),
          getTrackedEventWorkflow(eventId),
        ]);
        if (!cancelled) {
          setReleaseSource(source);
          setWorkflow(currentWorkflow);
          setSourceUrl(source.source_url ?? '');
          setSourceTitle(source.source_title ?? '');
          setError(null);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Julkaisutilan lataus epäonnistui.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadReleaseState();
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  async function submitReleaseSource() {
    if (!eventId || !releaseSource || submitting || processing) return;

    setSubmitMessage(null);
    setProcessMessage(null);

    const normalizedUrl = sourceUrl.trim();
    if (!normalizedUrl.startsWith('https://')) {
      setError('Syötä HTTPS-osoite.');
      return;
    }

    const normalizedActor = actor.trim();
    if (!normalizedActor) {
      setError('Syötä toimijan tunniste.');
      return;
    }

    const submittedEventId = eventId;
    setSubmitting(true);
    setError(null);

    try {
      const saved = await putTrackedEventReleaseSource(
        submittedEventId,
        {
          source_kind: releaseSource.active && releaseSource.source_kind
            ? releaseSource.source_kind
            : 'direct_url',
          source_url: normalizedUrl,
          ...(sourceTitle.trim() ? { source_title: sourceTitle.trim() } : {}),
          expected_version: releaseSource.version,
        },
        normalizedActor,
      );
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        setReleaseSource(saved);
        setSubmitMessage('Julkaisulähde tallennettu.');
      }
    } catch (submitError) {
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        const writeError =
          submitError instanceof Error ? submitError.message : 'Julkaisulähteen tallennus epäonnistui.';
        setError(writeError);

        try {
          const currentSource = await getTrackedEventReleaseSource(submittedEventId);
          if (mountedRef.current && eventIdRef.current === submittedEventId) {
            setReleaseSource(currentSource);
            setSourceUrl(currentSource.source_url ?? '');
            setSourceTitle(currentSource.source_title ?? '');
            setError(writeError);
          }
        } catch {
          // Keep the original write error visible if refreshing canonical state also fails.
        }
      }
    } finally {
      if (mountedRef.current && eventIdRef.current === submittedEventId) setSubmitting(false);
    }
  }

  async function processRelease() {
    if (!eventId || !canProcessRelease || processing || submitting) return;

    const normalizedActor = actor.trim();
    if (!normalizedActor) {
      setError('Syötä toimijan tunniste ennen julkaisun käsittelyä.');
      return;
    }

    const submittedEventId = eventId;
    setProcessing(true);
    setError(null);
    setSubmitMessage(null);
    setProcessMessage(null);

    try {
      const result = await ingestTrackedEventRelease(submittedEventId, normalizedActor);
      const [currentSource, currentWorkflow] = await Promise.all([
        getTrackedEventReleaseSource(submittedEventId),
        getTrackedEventWorkflow(submittedEventId),
      ]);
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        setReleaseSource(currentSource);
        setWorkflow(currentWorkflow);
        setSourceUrl(currentSource.source_url ?? '');
        setSourceTitle(currentSource.source_title ?? '');
        setProcessMessage(
          result.message?.trim()
            ? `${result.status}: ${result.message}`
            : `Käsittely valmis: ${result.status}`,
        );
      }
    } catch (processError) {
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        const writeError =
          processError instanceof Error ? processError.message : 'Julkaisun käsittely epäonnistui.';
        setError(writeError);
        try {
          const [currentSource, currentWorkflow] = await Promise.all([
            getTrackedEventReleaseSource(submittedEventId),
            getTrackedEventWorkflow(submittedEventId),
          ]);
          if (mountedRef.current && eventIdRef.current === submittedEventId) {
            setReleaseSource(currentSource);
            setWorkflow(currentWorkflow);
            setSourceUrl(currentSource.source_url ?? '');
            setSourceTitle(currentSource.source_title ?? '');
            setError(writeError);
          }
        } catch {
          // Keep the original ingestion error visible if canonical refresh also fails.
        }
      }
    } finally {
      if (mountedRef.current && eventIdRef.current === submittedEventId) setProcessing(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <BackButton />
      <Text style={styles.eyebrow}>WORKFLOW · JULKAISU</Text>
      <Text style={styles.title}>Julkaisun tarkistus</Text>
      <Text style={styles.body}>
        Tämä näkymä näyttää canonical tracked-event -workflow’hun liitetyn virallisen
        julkaisulähteen ja mahdollistaa yhden käyttäjän käynnistämän käsittely-yrityksen.
      </Text>

      <View style={styles.card}>
        <Text style={styles.label}>Tracked event</Text>
        <Text style={styles.value} selectable>
          {eventId || 'Tuntematon tapahtuma'}
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Julkaisulähde</Text>
        {loading ? <Text style={styles.value}>Ladataan…</Text> : null}
        {!loading && error ? <Text style={styles.error}>{error}</Text> : null}
        {!loading && releaseSource ? (
          releaseSource.active ? (
            <>
              <Text style={styles.status}>Aktiivinen lähde</Text>
              <Text style={styles.value}>{releaseSource.source_title || 'Nimetön lähde'}</Text>
              <Text style={styles.meta}>{releaseSource.source_kind}</Text>
              <Text style={styles.url} selectable>
                {releaseSource.source_url}
              </Text>
              <Text style={styles.meta}>Versio {releaseSource.version}</Text>
            </>
          ) : (
            <>
              <Text style={styles.status}>Ei aktiivista lähdettä</Text>
              <Text style={styles.meta}>Versio {releaseSource.version}</Text>
            </>
          )
        ) : null}
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Muuta julkaisulähdettä</Text>
        <TextInput
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          onChangeText={setSourceUrl}
          placeholder="https://…"
          placeholderTextColor="#677386"
          style={styles.input}
          value={sourceUrl}
        />
        <TextInput
          onChangeText={setSourceTitle}
          placeholder="Otsikko (valinnainen)"
          placeholderTextColor="#677386"
          style={styles.input}
          value={sourceTitle}
        />
        <TextInput
          autoCapitalize="none"
          autoCorrect={false}
          onChangeText={setActor}
          placeholder="Toimijan tunniste (tallennus / käsittely)"
          placeholderTextColor="#677386"
          style={styles.input}
          value={actor}
        />
        <Pressable
          disabled={!releaseSource || !sourceUrl.trim() || !actor.trim() || submitting || processing}
          onPress={() => void submitReleaseSource()}
          style={({ pressed }) => [
            styles.button,
            (!releaseSource || !sourceUrl.trim() || !actor.trim() || submitting || processing) && styles.buttonDisabled,
            pressed && styles.buttonPressed,
          ]}>
          <Text style={styles.buttonText}>{submitting ? 'Tallennetaan…' : 'Tallenna lähde'}</Text>
        </Pressable>
        {submitMessage ? <Text style={styles.success}>{submitMessage}</Text> : null}
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Julkaisun käsittely</Text>
        <Text style={styles.meta}>
          Workflow: {releaseStep?.status ?? 'tuntematon'}
        </Text>
        {releaseStep?.action_reason ? <Text style={styles.value}>{releaseStep.action_reason}</Text> : null}
        <Pressable
          disabled={!canProcessRelease || !actor.trim() || processing || submitting}
          onPress={() => void processRelease()}
          style={({ pressed }) => [
            styles.button,
            (!canProcessRelease || !actor.trim() || processing || submitting) && styles.buttonDisabled,
            pressed && styles.buttonPressed,
          ]}>
          <Text style={styles.buttonText}>{processing ? 'Käsitellään…' : 'Käsittele julkaisu'}</Text>
        </Pressable>
        {!releaseSource?.active ? (
          <Text style={styles.meta}>Lisää ensin aktiivinen hyväksytty julkaisulähde.</Text>
        ) : null}
        {releaseSource?.active && !canProcessRelease ? (
          <Text style={styles.meta}>Canonical workflow ei tällä hetkellä pyydä julkaisun käsittelyä.</Text>
        ) : null}
        {processMessage ? <Text style={styles.success}>{processMessage}</Text> : null}
      </View>

      <Text style={styles.note}>
        Käsittely tekee vain yhden explicit ingestion -yrityksen canonical backend -polun kautta. Se ei luo kaupankäyntitehtävää eikä käynnistä Strategy/Risk/Broker-polkuja.
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
  status: {
    color: '#f4f7fb',
    fontSize: 14,
    fontWeight: '700',
    marginTop: 8,
  },
  meta: {
    color: '#8994a6',
    fontSize: 12,
    marginTop: 5,
  },
  url: {
    color: '#b9c6d9',
    fontSize: 12,
    lineHeight: 18,
    marginTop: 8,
  },
  error: {
    color: '#f0a0a0',
    fontSize: 13,
    lineHeight: 19,
    marginTop: 8,
  },
  input: {
    backgroundColor: '#0d1118',
    borderColor: '#2a3342',
    borderRadius: 8,
    borderWidth: 1,
    color: '#f4f7fb',
    fontSize: 13,
    marginTop: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  button: {
    alignItems: 'center',
    backgroundColor: '#2d6cdf',
    borderRadius: 8,
    marginTop: 12,
    padding: 11,
  },
  buttonDisabled: {
    opacity: 0.45,
  },
  buttonPressed: {
    opacity: 0.8,
  },
  buttonText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '700',
  },
  success: {
    color: '#80d5a6',
    fontSize: 13,
    marginTop: 10,
  },
  note: {
    color: '#8994a6',
    fontSize: 12,
    lineHeight: 18,
    marginTop: 16,
  },
});
