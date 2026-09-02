import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { getPaperStatus, type PaperRun } from '@/services/api';
import {
  getTrackedEventPaperPermission,
  type TrackedEventPaperPermission,
} from '@/services/tracked-events';

type LoadState<T> =
  | { status: 'loading' }
  | { status: 'ready'; value: T }
  | { status: 'error' };

type Props = {
  eventId: string;
  expectationEventId: string | null;
};

export function TrackedEventPaperSummary({ eventId, expectationEventId }: Props) {
  const router = useRouter();
  const [permissionState, setPermissionState] = useState<LoadState<TrackedEventPaperPermission>>({
    status: 'loading',
  });
  const [executionState, setExecutionState] = useState<LoadState<PaperRun | null>>({
    status: 'loading',
  });

  useFocusEffect(
    useCallback(() => {
      let active = true;
      setPermissionState({ status: 'loading' });
      setExecutionState({ status: 'loading' });

      void getTrackedEventPaperPermission(eventId)
        .then((permission) => {
          if (active) setPermissionState({ status: 'ready', value: permission });
        })
        .catch(() => {
          if (active) setPermissionState({ status: 'error' });
        });

      if (!expectationEventId) {
        setExecutionState({ status: 'ready', value: null });
      } else {
        void getPaperStatus(expectationEventId)
          .then((status) => {
            if (active) setExecutionState({ status: 'ready', value: status.paper_run });
          })
          .catch(() => {
            if (active) setExecutionState({ status: 'error' });
          });
      }

      return () => {
        active = false;
      };
    }, [eventId, expectationEventId]),
  );

  return (
    <View style={styles.block}>
      <Text style={styles.title}>PAPER-kaupankäynti</Text>
      <PermissionSummary state={permissionState} />
      <ExecutionSummary state={executionState} />
      <Pressable
        style={styles.button}
        onPress={() =>
          router.push({
            pathname: '/tracked-events/[eventId]/release',
            params: { eventId },
          })
        }
      >
        <Text style={styles.buttonText}>PAPER-lupa ja asetukset</Text>
      </Pressable>
    </View>
  );
}

function PermissionSummary({ state }: { state: LoadState<TrackedEventPaperPermission> }) {
  if (state.status === 'loading') {
    return <Text style={styles.meta}>Ladataan lupaa…</Text>;
  }
  if (state.status === 'error') {
    return <Text style={styles.warning}>PAPER-luvan tilaa ei juuri nyt saatu haettua.</Text>;
  }

  const permission = state.value;
  if (permission.approval_current) {
    const cap = permission.max_position_value_usd;
    return (
      <Text style={styles.approved}>
        Lupa hyväksytty
        {cap !== null ? ` · max ${formatUsd(cap)} USD` : ''}
        {permission.approved_expectation_version !== null
          ? ` · expectation v${permission.approved_expectation_version}`
          : ''}
      </Text>
    );
  }
  if (permission.state === 'approved') {
    return (
      <Text style={styles.warning}>
        Lupa vanhentunut · nykyinen expectation v{permission.current_expectation_version}
      </Text>
    );
  }
  return (
    <Text style={styles.warning}>
      Lupa puuttuu · expectation v{permission.current_expectation_version}
    </Text>
  );
}

function ExecutionSummary({ state }: { state: LoadState<PaperRun | null> }) {
  if (state.status === 'loading') {
    return <Text style={styles.meta}>Kauppa: tarkistetaan…</Text>;
  }
  if (state.status === 'error') {
    return <Text style={styles.warning}>Kaupan tilaa ei juuri nyt saatu haettua.</Text>;
  }
  if (!state.value) {
    return <Text style={styles.meta}>Kauppa: ei vielä käsitelty.</Text>;
  }

  const run = state.value;
  if (run.status === 'paper_executed') {
    const order = run.paper_order;
    const details = [order?.direction, order?.quantity, order?.status]
      .filter((value) => value !== undefined && value !== null && String(value).trim() !== '')
      .join(' · ');
    return (
      <>
        <Text style={styles.approved}>Kauppa: PAPER-kauppa toteutettu</Text>
        {details ? <Text style={styles.meta}>{details}</Text> : null}
      </>
    );
  }
  if (run.status === 'waiting_confirmation') {
    return (
      <>
        <Text style={styles.warning}>Kauppa: odottaa vahvistuksia</Text>
        {run.message ? <Text style={styles.meta}>{run.message}</Text> : null}
      </>
    );
  }
  if (run.status === 'expired_no_trade') {
    return (
      <>
        <Text style={styles.meta}>Kauppa: NO TRADE · vahvistusaika päättyi</Text>
        {run.message ? <Text style={styles.meta}>{run.message}</Text> : null}
      </>
    );
  }

  return (
    <>
      <Text style={styles.meta}>Kauppa: {run.status ?? 'tila tuntematon'}</Text>
      {run.message ? <Text style={styles.meta}>{run.message}</Text> : null}
    </>
  );
}

function formatUsd(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

const styles = StyleSheet.create({
  block: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: '#202734',
  },
  title: {
    color: '#8590a1',
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  approved: {
    color: '#80d5a6',
    fontSize: 12,
    fontWeight: '700',
    marginTop: 4,
  },
  warning: {
    color: '#d7ad5f',
    fontSize: 12,
    fontWeight: '600',
    marginTop: 4,
  },
  meta: {
    color: '#8994a6',
    fontSize: 12,
    marginTop: 4,
  },
  button: {
    alignSelf: 'flex-start',
    marginTop: 9,
    borderWidth: 1,
    borderColor: '#315d48',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 7,
    backgroundColor: '#17231d',
  },
  buttonText: {
    color: '#80d5a6',
    fontSize: 12,
    fontWeight: '800',
  },
});
