import { useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { BackButton } from '@/components/back-button';
import {
  approveTrackedEventPaperPermission,
  getTrackedEventPaperPermission,
  getTrackedEventReleaseSource,
  getTrackedEventWorkflow,
  ingestTrackedEventRelease,
  putTrackedEventReleaseSource,
  skipTrackedEventRelease,
  type TrackedEventPaperPermission,
  type TrackedEventReleaseSource,
  type TrackedEventWorkflowResponse,
} from '@/services/tracked-events';

const DEFAULT_PAPER_POSITION_CAP_USD = '500';

function parsePositiveUsd(value: string): number | null {
  const parsed = Number(value.trim().replace(',', '.'));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export default function TrackedEventReleaseHandoffScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const [releaseSource, setReleaseSource] = useState<TrackedEventReleaseSource | null>(null);
  const [workflow, setWorkflow] = useState<TrackedEventWorkflowResponse | null>(null);
  const [paperPermission, setPaperPermission] = useState<TrackedEventPaperPermission | null>(null);
  const [loading, setLoading] = useState(true);
  const [permissionLoading, setPermissionLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const [sourceUrl, setSourceUrl] = useState('');
  const [sourceTitle, setSourceTitle] = useState('');
  const [actor, setActor] = useState('');
  const [maxPositionUsd, setMaxPositionUsd] = useState(DEFAULT_PAPER_POSITION_CAP_USD);
  const [skipReason, setSkipReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [approvingPermission, setApprovingPermission] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [processMessage, setProcessMessage] = useState<string | null>(null);
  const [skipMessage, setSkipMessage] = useState<string | null>(null);
  const [permissionMessage, setPermissionMessage] = useState<string | null>(null);
  const eventIdRef = useRef(eventId);
  const mountedRef = useRef(true);

  const releaseStep = workflow?.steps.find((step) => step.key === 'release') ?? null;
  const releaseActionRequired = Boolean(
    releaseStep?.status === 'action_required' && releaseStep.action_target === 'release',
  );
  const canProcessRelease = Boolean(releaseSource?.active && releaseActionRequired);
  const canSkipRelease = releaseActionRequired;
  const paperPermissionCurrent = Boolean(paperPermission?.approval_current);
  const parsedMaxPositionUsd = parsePositiveUsd(maxPositionUsd);

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
    setPermissionLoading(true);
    setReleaseSource(null);
    setWorkflow(null);
    setPaperPermission(null);
    setError(null);
    setPermissionError(null);
    setSourceUrl('');
    setSourceTitle('');
    setActor('');
    setMaxPositionUsd(DEFAULT_PAPER_POSITION_CAP_USD);
    setSkipReason('');
    setSubmitting(false);
    setProcessing(false);
    setSkipping(false);
    setApprovingPermission(false);
    setSubmitMessage(null);
    setProcessMessage(null);
    setSkipMessage(null);
    setPermissionMessage(null);

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

    async function loadPaperPermission() {
      if (!eventId) {
        setPermissionError('Tracked event puuttuu.');
        setPermissionLoading(false);
        return;
      }

      try {
        const permission = await getTrackedEventPaperPermission(eventId);
        if (!cancelled) {
          setPaperPermission(permission);
          if (permission.max_position_value_usd !== null) {
            setMaxPositionUsd(String(permission.max_position_value_usd));
          }
          setPermissionError(null);
        }
      } catch (loadError) {
        if (!cancelled) {
          setPermissionError(
            loadError instanceof Error ? loadError.message : 'PAPER-luvan lataus epäonnistui.',
          );
        }
      } finally {
        if (!cancelled) setPermissionLoading(false);
      }
    }

    void loadReleaseState();
    void loadPaperPermission();
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  async function approvePaperPermission(expectedVersion: number, positionCapUsd: number) {
    if (!eventId || approvingPermission || paperPermissionCurrent) return;
    const normalizedActor = actor.trim();
    if (!normalizedActor) {
      setPermissionError('Syötä toimijan tunniste ennen PAPER-luvan hyväksymistä.');
      return;
    }

    const submittedEventId = eventId;
    setApprovingPermission(true);
    setPermissionError(null);
    setPermissionMessage(null);

    try {
      const approved = await approveTrackedEventPaperPermission(
        submittedEventId,
        normalizedActor,
        {
          expected_expectation_version: expectedVersion,
          max_position_value_usd: positionCapUsd,
        },
      );
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        setPaperPermission(approved);
        if (approved.max_position_value_usd !== null) {
          setMaxPositionUsd(String(approved.max_position_value_usd));
        }
        setPermissionMessage('Kertaluonteinen PAPER-demokauppalupa hyväksytty.');
      }
    } catch (approvalError) {
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        const writeError =
          approvalError instanceof Error ? approvalError.message : 'PAPER-luvan hyväksyntä epäonnistui.';
        setPermissionError(writeError);
        try {
          const current = await getTrackedEventPaperPermission(submittedEventId);
          if (mountedRef.current && eventIdRef.current === submittedEventId) {
            setPaperPermission(current);
            setPermissionError(writeError);
          }
        } catch {
          // Keep the original approval error visible if canonical refresh also fails.
        }
      }
    } finally {
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        setApprovingPermission(false);
      }
    }
  }

  function confirmPaperPermission() {
    if (!paperPermission || paperPermissionCurrent || approvingPermission) return;
    const normalizedActor = actor.trim();
    if (!normalizedActor) {
      setPermissionError('Syötä toimijan tunniste ennen PAPER-luvan hyväksymistä.');
      return;
    }
    const positionCapUsd = parsePositiveUsd(maxPositionUsd);
    if (positionCapUsd === null) {
      setPermissionError('Syötä positiivinen enimmäispositio USD-määräisenä.');
      return;
    }
    const expectedVersion = paperPermission.current_expectation_version;
    Alert.alert(
      'Hyväksy PAPER-demokauppa',
      `Annat MarketAI:lle kertaluonteisen luvan tehdä ${paperPermission.instrument}-demokaupan tämän yhden tulosjulkistuksen perusteella. Expectation v${expectedVersion}. Enimmäispositio ${positionCapUsd} USD. Risk Engine voi käyttää tätä pienempää positiota. Kauppa tehdään vain, jos Strategy ja Risk Engine hyväksyvät sen.`,
      [
        { text: 'Peruuta', style: 'cancel' },
        {
          text: 'Hyväksy',
          onPress: () => void approvePaperPermission(expectedVersion, positionCapUsd),
        },
      ],
    );
  }

  async function submitReleaseSource() {
    if (!eventId || !releaseSource || submitting || processing || skipping || approvingPermission) return;

    setSubmitMessage(null);
    setProcessMessage(null);
    setSkipMessage(null);

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
    if (!eventId || !canProcessRelease || processing || submitting || skipping || approvingPermission) return;

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
    setSkipMessage(null);

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

  async function skipRelease() {
    if (!eventId || !canSkipRelease || skipping || processing || submitting || approvingPermission) return;

    const normalizedActor = actor.trim();
    if (!normalizedActor) {
      setError('Syötä toimijan tunniste ennen julkaisun ohittamista.');
      return;
    }
    const normalizedReason = skipReason.trim();
    if (!normalizedReason) {
      setError('Kirjoita syy julkaisun ohittamiselle.');
      return;
    }

    const submittedEventId = eventId;
    setSkipping(true);
    setError(null);
    setSubmitMessage(null);
    setProcessMessage(null);
    setSkipMessage(null);

    try {
      const result = await skipTrackedEventRelease(
        submittedEventId,
        normalizedActor,
        normalizedReason,
      );
      const [currentSource, currentWorkflow] = await Promise.all([
        getTrackedEventReleaseSource(submittedEventId),
        getTrackedEventWorkflow(submittedEventId),
      ]);
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        setReleaseSource(currentSource);
        setWorkflow(currentWorkflow);
        setSourceUrl(currentSource.source_url ?? '');
        setSourceTitle(currentSource.source_title ?? '');
        setSkipReason('');
        setSkipMessage(`Julkaisu ohitettu: ${result.status}`);
      }
    } catch (skipError) {
      if (mountedRef.current && eventIdRef.current === submittedEventId) {
        const writeError = skipError instanceof Error ? skipError.message : 'Julkaisun ohitus epäonnistui.';
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
          // Keep the original skip error visible if canonical refresh also fails.
        }
      }
    } finally {
      if (mountedRef.current && eventIdRef.current === submittedEventId) setSkipping(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <BackButton />
      <Text style={styles.eyebrow}>WORKFLOW · JULKAISU</Text>
      <Text style={styles.title}>Julkaisun tarkistus</Text>
      <Text style={styles.body}>
        Tämä näkymä näyttää canonical tracked-event -workflow’hun liitetyn virallisen
        julkaisulähteen, kertaluonteisen PAPER-kaupankäyntiluvan ja käyttäjän käynnistämät
        release-toimenpiteet.
      </Text>

      <View style={styles.card}>
        <Text style={styles.label}>Tracked event</Text>
        <Text style={styles.value} selectable>
          {eventId || 'Tuntematon tapahtuma'}
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Toimijan tunniste</Text>
        <Text style={styles.meta}>
          Tätä käytetään PAPER-luvan ja muiden tämän näkymän auditoitujen toimenpiteiden yhteydessä.
        </Text>
        <TextInput
          autoCapitalize="none"
          autoCorrect={false}
          onChangeText={setActor}
          placeholder="Esim. Marko"
          placeholderTextColor="#677386"
          style={styles.input}
          value={actor}
        />
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>PAPER-kaupankäyntilupa</Text>
        {permissionLoading ? <Text style={styles.value}>Ladataan…</Text> : null}
        {!permissionLoading && permissionError ? <Text style={styles.error}>{permissionError}</Text> : null}
        {!permissionLoading && paperPermission ? (
          <>
            <Text style={paperPermissionCurrent ? styles.permissionApproved : styles.status}>
              {paperPermissionCurrent
                ? 'Hyväksytty · PAPER · kertaluonteinen'
                : paperPermission.state === 'approved'
                  ? 'Hyväksyntä vanhentunut'
                  : paperPermission.state === 'pending'
                    ? 'Odottaa hyväksyntää'
                    : 'Ei hyväksytty'}
            </Text>
            <Text style={styles.value}>
              Lupa koskee vain {paperPermission.instrument}-instrumentin tätä yhtä tulosjulkistusta.
              Kauppa voidaan tehdä vain, jos Strategy ja Risk Engine hyväksyvät sen.
            </Text>
            <Text style={styles.meta}>
              Expectation v{paperPermission.current_expectation_version}
              {paperPermission.approved_expectation_version
                ? ` · hyväksytty v${paperPermission.approved_expectation_version}`
                : ''}
            </Text>
            {paperPermission.max_position_value_usd !== null && paperPermissionCurrent ? (
              <Text style={styles.meta}>
                Hyväksytty enimmäispositio {paperPermission.max_position_value_usd} USD
              </Text>
            ) : null}
            {paperPermission.approved_by ? (
              <Text style={styles.meta}>
                Hyväksyjä {paperPermission.approved_by}
                {paperPermission.approved_at
                  ? ` · ${new Date(paperPermission.approved_at).toLocaleString('fi-FI')}`
                  : ''}
              </Text>
            ) : null}
            <Text style={styles.subLabel}>Enimmäispositio (USD)</Text>
            <Text style={styles.meta}>
              Risk Engine saa käyttää tätä pienempää positiota, mutta ei tätä suurempaa.
            </Text>
            <TextInput
              editable={!paperPermissionCurrent && !approvingPermission}
              keyboardType="decimal-pad"
              onChangeText={setMaxPositionUsd}
              placeholder="500"
              placeholderTextColor="#677386"
              style={styles.input}
              value={maxPositionUsd}
            />
            {parsedMaxPositionUsd === null ? (
              <Text style={styles.error}>Syötä positiivinen USD-määrä.</Text>
            ) : null}
            <Pressable
              disabled={
                paperPermissionCurrent ||
                !actor.trim() ||
                parsedMaxPositionUsd === null ||
                approvingPermission ||
                submitting ||
                processing ||
                skipping
              }
              onPress={confirmPaperPermission}
              style={({ pressed }) => [
                styles.button,
                paperPermissionCurrent && styles.permissionButtonApproved,
                (paperPermissionCurrent ||
                  !actor.trim() ||
                  parsedMaxPositionUsd === null ||
                  approvingPermission ||
                  submitting ||
                  processing ||
                  skipping) && styles.buttonDisabled,
                pressed && styles.buttonPressed,
              ]}>
              <Text style={styles.buttonText}>
                {approvingPermission
                  ? 'Hyväksytään…'
                  : paperPermissionCurrent
                    ? 'PAPER-lupa hyväksytty'
                    : paperPermission.state === 'approved'
                      ? 'Hyväksy nykyinen versio'
                      : 'Hyväksy demokauppa'}
              </Text>
            </Pressable>
            {permissionMessage ? <Text style={styles.success}>{permissionMessage}</Text> : null}
          </>
        ) : null}
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
        <Pressable
          disabled={!releaseSource || !sourceUrl.trim() || !actor.trim() || submitting || processing || skipping || approvingPermission}
          onPress={() => void submitReleaseSource()}
          style={({ pressed }) => [
            styles.button,
            (!releaseSource || !sourceUrl.trim() || !actor.trim() || submitting || processing || skipping || approvingPermission) && styles.buttonDisabled,
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
          disabled={!canProcessRelease || !actor.trim() || processing || submitting || skipping || approvingPermission}
          onPress={() => void processRelease()}
          style={({ pressed }) => [
            styles.button,
            (!canProcessRelease || !actor.trim() || processing || submitting || skipping || approvingPermission) && styles.buttonDisabled,
            pressed && styles.buttonPressed,
          ]}>
          <Text style={styles.buttonText}>{processing ? 'Käsitellään…' : 'Käsittele julkaisu'}</Text>
        </Pressable>
        {!releaseSource?.active ? (
          <Text style={styles.meta}>Lisää ensin aktiivinen hyväksytty julkaisulähde käsittelyä varten.</Text>
        ) : null}
        {releaseSource?.active && !canProcessRelease ? (
          <Text style={styles.meta}>Canonical workflow ei tällä hetkellä pyydä julkaisun käsittelyä.</Text>
        ) : null}
        {processMessage ? <Text style={styles.success}>{processMessage}</Text> : null}

        <Text style={styles.subLabel}>Ohita julkaisu</Text>
        <Text style={styles.meta}>
          Ohitus on auditoitu päätös. Sitä voi käyttää vain, kun canonical workflow pyytää release-toimenpidettä.
        </Text>
        <TextInput
          multiline
          onChangeText={setSkipReason}
          placeholder="Ohituksen syy"
          placeholderTextColor="#677386"
          style={[styles.input, styles.reasonInput]}
          value={skipReason}
        />
        <Pressable
          disabled={!canSkipRelease || !actor.trim() || !skipReason.trim() || skipping || processing || submitting || approvingPermission}
          onPress={() => void skipRelease()}
          style={({ pressed }) => [
            styles.button,
            styles.skipButton,
            (!canSkipRelease || !actor.trim() || !skipReason.trim() || skipping || processing || submitting || approvingPermission) && styles.buttonDisabled,
            pressed && styles.buttonPressed,
          ]}>
          <Text style={styles.buttonText}>{skipping ? 'Ohitetaan…' : 'Ohita julkaisu'}</Text>
        </Pressable>
        {!canSkipRelease ? (
          <Text style={styles.meta}>Canonical workflow ei tällä hetkellä salli julkaisun ohitusta.</Text>
        ) : null}
        {skipMessage ? <Text style={styles.success}>{skipMessage}</Text> : null}
      </View>

      <Text style={styles.note}>
        PAPER-luvan hyväksyntä koskee vain tätä eventtiä, vahvistettua expectation-versiota ja hyväksyttyä enimmäispositiota. Se ei pakota kauppaa: Strategy, markkinavahvistukset ja Risk Engine voivat edelleen päätyä NO TRADEen. Julkaisun käsittely ja ohitus pysyvät erillisinä auditoituina toimintoina.
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
  subLabel: {
    color: '#d8dee8',
    fontSize: 12,
    fontWeight: '700',
    marginTop: 22,
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
  permissionApproved: {
    color: '#80d5a6',
    fontSize: 14,
    fontWeight: '800',
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
  reasonInput: {
    minHeight: 76,
    textAlignVertical: 'top',
  },
  button: {
    alignItems: 'center',
    backgroundColor: '#2d6cdf',
    borderRadius: 8,
    marginTop: 12,
    padding: 11,
  },
  permissionButtonApproved: {
    backgroundColor: '#315d48',
  },
  skipButton: {
    backgroundColor: '#623f52',
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
