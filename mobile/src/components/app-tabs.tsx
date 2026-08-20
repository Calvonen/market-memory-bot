import { NativeTabs } from 'expo-router/unstable-native-tabs';
import { useColorScheme } from 'react-native';

import { Colors } from '@/constants/theme';

export default function AppTabs() {
  const scheme = useColorScheme();
  const colors = Colors[scheme === 'unspecified' ? 'light' : scheme];

  return (
    <NativeTabs
      backgroundColor={colors.background}
      indicatorColor={colors.backgroundElement}
      labelStyle={{ selected: { color: colors.text } }}>
      <NativeTabs.Trigger name="index">
        <NativeTabs.Trigger.Label>Tapahtumat</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon
          src={require('@/assets/images/tabIcons/home.png')}
          renderingMode="template"
        />
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="memory"><NativeTabs.Trigger.Label>Memory</NativeTabs.Trigger.Label><NativeTabs.Trigger.Icon src={require('@/assets/images/tabIcons/explore.png')} renderingMode="template" /></NativeTabs.Trigger>
      <NativeTabs.Trigger name="scanner"><NativeTabs.Trigger.Label>Scanneri</NativeTabs.Trigger.Label><NativeTabs.Trigger.Icon src={require('@/assets/images/tabIcons/explore.png')} renderingMode="template" /></NativeTabs.Trigger>
      <NativeTabs.Trigger name="trades"><NativeTabs.Trigger.Label>Tradet</NativeTabs.Trigger.Label><NativeTabs.Trigger.Icon src={require('@/assets/images/tabIcons/explore.png')} renderingMode="template" /></NativeTabs.Trigger>
      <NativeTabs.Trigger name="settings"><NativeTabs.Trigger.Label>Asetukset</NativeTabs.Trigger.Label><NativeTabs.Trigger.Icon src={require('@/assets/images/tabIcons/explore.png')} renderingMode="template" /></NativeTabs.Trigger>
    </NativeTabs>
  );
}
