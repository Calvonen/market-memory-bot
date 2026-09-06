import { Link } from 'expo-router';
import { Pressable, Text, View } from 'react-native';

import { ScreenShell, shared } from '@/components/screen-shell';

export default function TradesScreen() {
  return (
    <ScreenShell title="Tradet" subtitle="DEMO-kaupankäynnin tila ja valmisteltavat ehdotukset">
      <View style={shared.card}>
        <Text style={shared.heading}>DEMO</Text>
        <Text style={shared.text}>
          Avoimen tapahtumatraden tila ja riskipäätös näkyvät Etusivu / Tapahtumat -näkymässä.
          Backendin PAPER-lifecycle säilyy muuttumattomana.
        </Text>
      </View>

      <View style={shared.card}>
        <Text style={shared.heading}>Valmisteltavat ehdotukset</Text>
        <Text style={shared.text}>
          Tarkista assistentin valmistelema tapahtuma ja strategia ennen canonical seurantaan lisäämistä.
          LIVE näkyy tarkistuksessa lukittuna eikä sitä voi ottaa käyttöön.
        </Text>
        <Link href="/proposals" asChild>
          <Pressable style={shared.button}>
            <Text style={shared.buttonText}>Avaa ehdotukset</Text>
          </Pressable>
        </Link>
      </View>
    </ScreenShell>
  );
}
