/*
  Fixed data for the start screen. Banners are NOT here: they are in js/banners.js, which
  banner_tracker_test.py writes (see README.md, "Banner data").

  previewNewest   ?preview=newest : two Genshin characters that are not on a banner now, shown only to
                  check the element backgrounds with real art. Same slide shape as js/banners.js.
  sampleHistory   ?state=synced   : sample pity values (NOT the user's data).
*/
window.PT_DATA = {
  /* ?preview=newest : the newest Genshin characters from the tracker (not on a banner now).
     Shown only to check the element backgrounds with real art. */
  previewNewest: {
    genshin: [
      { id: "gi-vesna", group: "character", kind: "character", banner: "Character Event Wish", preview: true,
        name: "Vesna", element: "Anemo",
        art: "assets/art/genshin/vesna-art.webp", icon: "assets/art/genshin/vesna-icon.webp",
        desc: "Commander of the Druzhna and the Tsaritsa's keen blade." },
      { id: "gi-vodyanitsa", group: "character", kind: "character", banner: "Character Event Wish", preview: true,
        name: "Vodyanitsa", element: "Hydro",
        art: "assets/art/genshin/vodyanitsa-art.webp", icon: "assets/art/genshin/vodyanitsa-icon.webp",
        desc: "Prima Donna of the Korolevskiy Troupe and a Water Imp who has parted from her kin." }
    ]
  },

  /* ?state=synced : sample pity values (NOT the user's data). Same figures as the IA mockups. */
  sampleHistory: {
    account: "7xxxx1234",
    syncedMinutesAgo: 11,
    pity: {
      genshin: { character: { pity: 62, guarantee: false }, weapon: { pity: 31, guarantee: false } },
      hsr:     { character: { pity: 18, guarantee: true },  lightcone: { pity: 9, guarantee: false } },
      zzz:     { exclusive: { pity: 44, guarantee: false }, wengine: { pity: 12, guarantee: false } }
    }
  }
};
