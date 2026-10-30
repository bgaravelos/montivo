# Montivo guide (what Monti answers from)

Monti, the gecko in the corner, answers "where is X?" and "how do I do Y?" from this file only (montivo/monti.py reads
it). Each section starts with a `monti:` line: `target` is the place on screen Show me points at (one of
montivo/monti.py TARGETS), `panel` the rail panel it opens, `words` the keywords the $0 fallback matches without the
model. The first paragraph of a section is its short answer and the numbered lines are its steps. UI names are written
exactly as the app shows them. Keep it true to the app: when a button changes, change this file.

## What Montivo is
<!-- monti: target=viewer.player words=montivo,what is montivo,the app,εφαρμογή,τι κάνει το,τι είναι το,πρόγραμμα -->
Montivo is an agent-first video editor: Muse Spark 1.3 watches your footage, plans a vertical cut, renders it, then reviews its own render blind and re-cuts it until it passes or a guard stops it.
1. A new project goes: upload, Muse Voice (words and speakers), SAM 3.1 (tracks the speaker's head), watch, plan, render, review, fix, render again.
2. Every change, the agent's or yours, is a new version; nothing is overwritten and undo walks back through the versions.
3. Ask Montivo (the chat in the Assistant panel) edits the cut for you. Monti (me) only explains where things are and how they work.
Every runtime model is a Meta model; every call is logged with its cost in the run's ledger.json.

## Models per stage
<!-- monti: target=top.more words=model,models,meta,spark,voice,sam,image,open,baseten,llama,μοντέλο,μοντέλα,which model,ποιο μοντέλο -->
Each stage runs on one Meta model: Muse Spark 1.3 watches, plans, reviews and runs Ask Montivo; Muse Voice transcribes; SAM 3.1 tracks; Muse Image draws.
1. Muse Spark 1.3: watch (shot log, speaker map), plan (the paper edit), the blind review with fix ops, Ask Montivo's router, style from words, cover design, post text, title hooks, and Monti's answers.
2. Muse Spark Open (open weights, on Baseten): the plan, when you pick "Spark Open" in the planner menu (New project, or Ask Montivo's composer). About $0.012 and 7 s instead of $0.05 and 13 s. It does not review: it cannot hear the cut.
3. Muse Voice Transcribe 1.0: the words, their times and the speakers of the footage, and each rendered cut again so the review can check the edge words.
4. SAM 3.1: the speaker's head and person tracks for the 9:16 reframe, the face on close-ups, a microphone, and the cutout on a cover.
5. Muse Image 1.0: a title card in the brand colours and B-roll stills ($0.01 each).
ffmpeg and Pillow do the cutting, captions, music and loudness: no model, $0.

## Open weight: Muse Spark Open
<!-- monti: target=rail.assistant panel=assistant words=open weight,open,baseten,spark open,open model,ανοιχτό,ανοιχτού κώδικα,open-weight -->
Muse Spark Open plans the cut (the paper edit) when you choose it; everything after the plan is the same code for both planners.
1. On a new project: New project, Advanced, Planner: Spark Open.
2. On an open project: the planner menu in the Ask Montivo composer, then ask for a re-plan ("make it about 30 seconds").
3. It needs BASETEN_API_KEY on the server (the hosted demo has it).
The plan is the one stage that needs no picture or sound, so it runs on the open model; watch and review stay on Spark 1.3 because they must see and hear the cut.

## Start a new project
<!-- monti: target=home.new words=new project,upload,new,start,footage,clip,create,run,νέο,ανέβασ,ανεβάζ,φόρτωσ,ξεκιν,καινούρ,βίντεο μου,my video,my clip,my own -->
On the project list, press New project (key N), pick or drop a clip, write a one-line brief and press Create.
1. Drop a video anywhere on the project list, or choose one of the footage thumbnails.
2. Pick what kind of video it is (talking head, interview, product demo, event recap) and a length chip; the brief is one sentence on what the cut should say.
3. Advanced: review loop on or off, hook A/B, punch-ins, the planner (Spark 1.3 or Spark Open), a brand kit.
4. Create (⌘Enter). The sheet shows the price and time before you start: about $0.045 plus $0.0026 per second of footage, plus about $0.05 for the review loop.
On the hosted demo a clip can be up to 90 s; a first run of a minute of footage takes about 2 minutes.

## The project list (Home)
<!-- monti: target=home.grid words=home,projects,project list,all projects,samples,yours,tiles,archive,αρχική,έργα,projects,λίστα,δείγματα -->
Home lists every project as a 9:16 tile with its score; the cut plays when you hover it, and a click opens the editor.
1. The hero at the top plays the best cut beside its loop: each reviewed version, its score, its worst note and the fix between two.
2. Samples / Yours (on the hosted demo): the finished samples everyone shares, and your own projects and copies.
3. The search box (key /) searches names, runs, footage and briefs.
4. The archive button on a tile moves it to Archived (Undo in the toast, Restore under Archived); nothing is ever deleted.
In the editor, the back arrow at the top left returns here.

## The editor layout
<!-- monti: target=viewer.player words=layout,editor layout,interface,screen layout,οθόνη,παράθυρο,διάταξη -->
The editor has a top bar, the icon rail on the left with one panel open, the 9:16 viewer in the middle, the inspector on the right and the timeline at the bottom.
1. Rail panels (keys 1 to 8): Assistant, Media, Captions, Style, Text, Music, Speakers, Versions.
2. Top bar: undo / redo, the score chip, Loop, the More menu, Share, Export.
3. Viewer: Live / Rendered switch above it, the transport below it.
4. Inspector: whatever is selected (a clip, a caption line, a callout, the music), else the review of the version.
Under 900 px (a phone) the editor is a review mode: player, review, chat and Export only.

## Versions, undo and redo
<!-- monti: target=top.undo words=undo,redo,version,versions,back,previous,history,αναίρεση,πίσω,έκδοση,εκδόσεις,προηγούμεν,ιστορικό,restore,επαναφορά -->
Every edit makes a new version, so ⌘Z steps back to the previous version and ⇧⌘Z forward; nothing is lost.
1. Undo and redo are the arrows at the top left (⌘Z, ⇧⌘Z). In Live they first undo the draft's own edits.
2. An edit made after stepping back branches from that version; the newer ones stay in Versions.
3. The Versions panel (key 8) lists every version with its poster, score and what made it; star or rename one (double-click the name), filter All / Agent / You.
4. Restore on an older version copies it forward as the newest version, with its review.

## Versions panel
<!-- monti: target=rail.versions panel=versions words=versions panel,star,rename,name a version,compare,restore,εκδόσεις,σύγκριση,αστέρι,όνομα έκδοσης -->
Versions (rail, key 8) lists every version with a clean poster, its blind score and one line on what made it.
1. Click a version to open it; the line of blind scores at the top picks one too.
2. All / Agent / You filters by who made it; a star keeps a version visible under every filter; double-click the name to rename it.
3. Compare plays two versions side by side on one transport.
4. Restore copies an older version forward as the newest.

## Live and Rendered
<!-- monti: target=viewer.mode words=live,rendered,draft,make version,discard,preview,render,ζωντανά,προεπισκόπηση,draft,πρόχειρο,make version -->
Live plays edits at once straight from the footage; Rendered plays the exported file of the version. The switch is above the viewer (key V).
1. In Live, cuts, trims, moves, deletes and the panels' edits go into a draft that plays at once, in milliseconds and $0.
2. The top bar then shows Discard and Make Version N: one render of every edit in the draft. Nothing renders until you press it.
3. ⌘Z in Live undoes the draft's last edit.
4. Rendered shows exactly the file you would download; each edit there makes one version and renders it.

## Play and navigate (transport)
<!-- monti: target=viewer.transport words=play,pause,transport,speed,frame,timecode,loop,mute,fullscreen,full screen,παίξ,παύση,ταχύτητα,καρέ,σίγαση,πλήρη οθόνη -->
The transport under the viewer plays, steps frames, jumps cuts, changes speed, loops, mutes and goes full screen.
1. Space plays and pauses; J / K / L shuttle back, stop, forward (again for 2x, 4x).
2. ← → one frame, ⇧← ⇧→ one second, ↑ ↓ the previous / next cut.
3. Click the timecode to switch between seconds and frames.
4. On the right: speed (1, 1.5, 2, 0.5), loop the cut, mute (⇧M), safe area (G), full screen (F).

## Safe-area guides
<!-- monti: target=viewer.safe words=safe area,safe,reels,tiktok,shorts,ui,overlay,buttons,ασφαλ,περιθώρι -->
The safe-area guides show where Reels, TikTok and Shorts cover the picture with their own buttons. Press G to cycle them.
1. G (or the shield button in the transport) cycles Reels, TikTok, Shorts, off.
2. Keep captions and callouts out of the shaded bands.
3. Export's pre-flight counts the caption lines that reach into each app's UI.

## Keyboard shortcuts
<!-- monti: target=top.more words=shortcut,shortcuts,keys,keyboard,hotkey,πλήκτρ,συντομεύσ,κουμπιά πληκτρολογίου -->
The shortcuts are in More (top bar) > Keyboard shortcuts; the command palette (⌘P or /) runs any command by name.
1. Space play, J K L shuttle, ← → frame, ↑ ↓ cut, S or ⌘K split, [ ] select left / right, ⌘A every clip.
2. ⌫ deletes the selection, N snapping, . and , next / previous reviewer issue, V Live / Rendered, G safe area, F full screen.
3. 1 to 8 open the panels; ⌘Z / ⇧⌘Z undo / redo; ⌘+ / ⌘− zoom the timeline; Esc clears the selection.
4. ? opens Monti.

## Command palette
<!-- monti: target=viewer.player words=palette,command,command palette,search command,εντολ,παλέτα -->
⌘P (or /) opens the command palette: type a few letters of any command (a panel, split, undo, review, export) and press Enter.

## Timeline
<!-- monti: target=tl.video words=timeline,track,tracks,lanes,clip,clips,ruler,γραμμή χρόνου,timeline,κλιπ,κομμάτια,track -->
The timeline at the bottom shows the cut as lanes: B-roll, Text (callouts), Captions, Video, Voice and Music, with the reviewer's issues on the ruler.
1. Click a clip to select it; ⇧ click selects a run, ⌘ click adds one, ⌘A selects all.
2. Drag a clip's edge to trim (it snaps to word boundaries); drag its body to move it.
3. The bar above the lanes: split, loop the clip, ask Montivo about it, delete, and "N pauses, −X s".
4. Zoom with ⌘+ / ⌘− or the slider; the magnet turns snapping on or off (N).

## Trim a clip
<!-- monti: target=tl.video words=trim,shorten,edge,start,end,drag edge,κόψιμο,κόψε την αρχή,τέλος,άκρη,μίκρυν,trim -->
Drag a clip's left or right edge on the Video lane: it snaps to a word, and a version (or, in Live, a draft edit) is made.
1. Hover the clip's edge until the cursor changes, drag, release.
2. Trims always land on a word boundary and are then tightened to the speech.
3. Hold ⇧ to drag free of the snapping to the playhead and issue markers.

## Split a clip
<!-- monti: target=tl.tools words=split,cut here,cut at,razor,blade,divide,χώρισ,σπάσ,split,κόψε στη μέση -->
Put the playhead where you want the cut and press S (or ⌘K, or the scissors in the timeline bar): the clip splits in front of the nearest word.
1. The cut plays the same; select either piece to move or delete it.

## Delete a clip or words
<!-- monti: target=tl.tools words=delete,remove,drop,cut out,erase,σβήσ,σβην,διαγραφ,διαγράψ,αφαίρεσ,βγάλε,remove clip,delete words,λέξη,λέξεις -->
Select a clip and press ⌫ (or the bin in the timeline bar). For single words, use the Transcript tab, select the words and press ⌫.
1. A whole clip: click it, ⌫. Several: ⇧ or ⌘ click them, ⌫.
2. Words: Transcript tab, click or drag over words (⇧ click extends), ⌫. Words at a clip's edge trim it, words inside split it.
3. In Rendered a confirm shows what each clip loses; in Live it goes straight into the draft (⌘Z undoes it).
The hook (the first clip) cannot be dropped whole.

## Move clips (reorder)
<!-- monti: target=tl.video words=move,reorder,order,drag clip,swap,before,after,μετακίνησ,σειρά,άλλαξε σειρά,reorder -->
Drag a clip's body on the Video lane to a new place; the cuts that now meet are tightened again and the roles (hook, body, payoff) follow the new order.

## Transcript editing
<!-- monti: target=tl.tabs words=transcript,text edit,words,document,edit text,κείμενο,απομαγνητοφώνησ,λέξεις,transcript -->
The Transcript tab (beside Timeline, above the lanes) edits the cut like a document: select words and press ⌫.
1. Click, drag or ⇧ click words in the transcript.
2. ⌫ deletes them: a whole clip is dropped, words at an edge trim, words inside split the clip.
3. "Not in the cut" shows the passages the plan left out: Insert puts one back in source order, At the playhead puts it there.

## Put a passage back (insert)
<!-- monti: target=rail.media panel=media words=insert,add back,bring back,put back,unused,left out,missing line,ξαναβάλε,επανέφερε,πρόσθεσε πίσω,κομμάτι που κόπηκε,ότι έλειψε -->
Open Media (key 2), Source tab: the whole recording turn by turn, with the words in the cut bright and the rest dim. Click and ⇧ click an unused passage, then Insert or At the playhead.
1. The Transcript tab's "Not in the cut" does the same, and a ghost clip on the timeline has "Bring it back".
2. Search finds a word anywhere in the recording.

## Remove pauses
<!-- monti: target=tl.tools words=pause,pauses,silence,gaps,dead air,tighten,παύσ,σιωπ,κενά,σφίξε -->
The "N pauses, −X s" button in the timeline bar cuts the pauses the audio has between words, at the nearest word edge; no word is lost.

## Media panel and the brief
<!-- monti: target=rail.media panel=media words=media,source,brief,assets,re-plan brief,recording,πηγή,υλικό,brief,assets -->
Media (rail, key 2) has three tabs: Source (the whole recording), Brief (edit it and re-plan) and Assets (B-roll stills, title card, covers, music bed, the clip's facts).
1. Brief: change the text and press "Re-plan with this brief" (about $0.05, a new plan and version).
2. Assets lists what this version uses and the covers you made on Export.

## Re-plan or change the length
<!-- monti: target=chat.composer panel=assistant words=replan,re-plan,shorter,longer,length,seconds,30 seconds,new plan,ξαναφτιάξ,πιο σύντομο,μικρότερο,διάρκεια,δευτερόλεπτα,νέο plan -->
A new length or a new angle is a re-plan: ask in the Ask Montivo chat ("make it about 30 seconds") or edit the brief in Media and press Re-plan.
1. A re-plan is shown first as a card with the change, cost (~$0.05 on Spark 1.3, ~$0.012 on Spark Open) and time; nothing runs until Apply.
2. Caption fixes, callouts, the look and music carry over to the new plan.

## Ask Montivo (the chat that edits)
<!-- monti: target=chat.composer panel=assistant words=ask montivo,chat,assistant,agent,instruction,tell it,ζήτα,συνομιλία,chat,βοηθός,εντολή -->
Ask Montivo, the chat in the Assistant panel (key 1), edits the cut from a message; Monti only explains the app.
1. Type what you want ("put lo-fi under it and make the captions bolder", Greek or English) and send.
2. One Muse Spark 1.3 call (about $0.006) picks the edits; music, look, captions and callouts apply at once.
3. A structural change (re-plan, delete, move, cut words) or anything over $0.02 comes back as a card first: Apply or Try something else.
4. Select clips, words or a caption line first and "this" means that selection.
5. "Where do I say X?" moves the playhead to the words and selects them. In Live its edits go into the draft.
The composer has the planner menu, a Review toggle (review the new version, ~$0.035) and the cost; a message typed during a job waits for it.

## Captions: fix a word or line
<!-- monti: target=rail.captions panel=captions words=caption,captions,subtitle,subtitles,wrong word,typo,misheard,fix caption,retext,υπότιτλ,λάθος λέξη,διόρθωσ,λεζάντ,captions -->
Open Captions (key 3), double-click the line (or its pencil), type the right words, then Make Version N (in Live it goes into the draft).
1. Or on the timeline: double-click a line on the Captions lane to retext it in place; Enter opens its box.
2. "Correct all" / Find and replace fixes a word everywhere (the run's glossary, kept by re-plans).
3. "Lines to check" lists the lines the reviewer or Voice disagree on.
4. "Did you say ...?" suggests names Voice may have misheard: Accept fixes them everywhere, never unasked.
5. Enter at a word splits a line, ⌫ at a line's start joins it to the one before.
A line you retexted is marked checked: the reviewer cannot fail the cut on it again.

## Caption look (Style)
<!-- monti: target=rail.style panel=style words=style,look,font,bigger,colour,color,caption style,preset,karaoke,bold,size,position,στυλ,γραμματοσειρά,μεγαλύτερα,χρώμα,εμφάνιση,θέση,karaoke -->
Open Style (key 4): pick a preset card (Marker, Clean, Box, Karaoke, Bold pop, Minimal) or tune the tabs Text, Active word and Position, then Apply.
1. Describe it: write the look in words ("big yellow, two words a line") and Muse Spark picks it (~$0.008).
2. Text: font, size, colours, outline, case, words per line, line breaks. Active word: pill, colour, karaoke or none.
3. Position: auto (clear of the face) or fixed; drag the caption on the paused viewer.
4. Applies to: Whole cut or This clip.
5. Save look keeps it under "Your looks"; with music and a title card it is a brand kit (Apply kit, or pick it on New project).
In Live the staged look plays at once before you apply it.

## Callouts and the title (Text)
<!-- monti: target=rail.text panel=text words=callout,callouts,title,title card,header,hook text,headline,text on screen,τίτλος,κείμενο στην οθόνη,callout,επικεφαλίδα -->
Open Text (key 5) to rewrite, add or remove callouts and to set the title.
1. Callouts: edit the words of a clip's callout, "" removes it; "New callout at the playhead" adds one (or double-click the Text lane).
2. Select a callout on the timeline for its inspector: words, colours (every callout or only this one), place, timing (−0.5 s, at the playhead, +0.5 s); drag it on the paused viewer to pin it.
3. Title: "Over the opening" (a pill over the first seconds of speech, the default for new projects) or "Card before" (a Muse Image card, $0.01 for new words).
4. "Suggest 3 hooks" asks Muse Spark for three title lines (~$0.009).

## Music
<!-- monti: target=rail.music panel=music words=music,song,track,soundtrack,bed,lofi,lo-fi,background music,μουσική,τραγούδι,κομμάτι,υπόκρουση,lofi -->
Open Music (key 6), press ▶ on a track to hear it under the cut, then apply it.
1. Eight CC0 tracks (lo-fi, hip hop, funk, city-pop, acoustic, ukulele and more); in Live ▶ plays the cut from the playhead with that bed.
2. Level: Quiet, Balanced or Present (the dB are under Details); the music ducks under the voice by itself.
3. Starts in the loop at, and Fade out at the end.
4. On the Music lane: drag across a stretch, then Lift or Hold down; click a band to remove it.
5. The Voice and Music faders at the left of the timeline balance them live before Make Version.
Music is $0: no model, no licence fee.

## Voice and music balance (faders)
<!-- monti: target=tl.voice words=fader,faders,volume,loud,quiet,louder,balance,mix,level,ένταση,δυνατά,σιγά,φωνή,μίξη -->
The Voice and Music faders at the left of the timeline set the balance between speech and music; you hear it live, then Make Version (or Apply in Live) renders it.
1. The cut is always normalised to −14 LUFS, so the faders move the balance, not the overall loudness.
2. With no music there is nothing to balance and the faders are off.

## Speakers and framing
<!-- monti: target=rail.speakers panel=speakers words=speaker,speakers,face,head,framing,crop,reframe,follow,who,camera,πρόσωπο,ομιλητ,κάδρο,κεφάλι,ποιος μιλάει,reframe -->
Open Speakers (key 7) to name speakers and choose which head the 9:16 crop follows.
1. Every Voice speaker shows the head the speaker map gave it ("watch's guess"); pick another head to pin it.
2. With the panel open and the video paused, click a face on the viewer to stage that head.
3. Name a speaker ("A is Billy"); merge two speakers that are one person.
The crop follows the talking speaker's head, tracked by SAM 3.1.

## Punch-ins
<!-- monti: target=insp words=punch,punch-in,punch in,zoom,zoom in,emphasis,ζουμ,punch,έμφαση -->
Punch-ins are small zooms (1.12x on emphasised words, 1.06x on jump cuts). Turn them on or off with the switch in the clip inspector; pink markers on the Video lane show each one.
1. Select a pink marker to see its word and frame; Remove (or ⌫) takes that one off.
2. Style's highlight words decide which words get one.

## B-roll stills
<!-- monti: target=tl.broll words=b-roll,broll,image,still,picture,overlay,cutaway,εικόνα,φωτογραφία,b-roll,πλάνο -->
The + on the B-roll lane asks Muse Image for a still from a prompt ($0.01) and lays it under the captions at the playhead, tied to its words.
1. Placement: full frame, card or corner (away from the head).
2. Moving, resizing and removing it are $0.

## The review and the score
<!-- monti: target=top.score words=score,review,reviewer,rating,points,100,pass,notes,issues,βαθμός,σκορ,κριτική,review,αξιολόγηση,περνάει -->
After each render Muse Spark 1.3 reviews the cut blind (it never sees the plan) against a rubric and scores it out of 100; the score chip in the top bar shows the best version.
1. Select nothing to see the review in the inspector: the score, each criterion, and every issue with its time, the fix op and its result.
2. . and , jump to the next / previous issue; the markers on the ruler are the issues.
3. "Review this version" asks for a new blind review (~$0.035).
4. The same cut reviewed twice can differ by a few points.

## The pass gate
<!-- monti: target=insp words=gate,pass gate,measured,checks,blocked,held,why no pass,doesn't pass,δεν περνάει,gate,έλεγχοι,μετρημένα -->
The gate holds a pass the reviewer would give when a measured check fails or a blocker / major note is still open; the inspector shows "gate" with each reason tagged measured or reviewer.
1. Measured checks: a cut edge inside speech, a kept edge word Muse Voice no longer hears in the cut, a caption forced over the head, the head out of the 9:16 frame for 0.5 s.
2. Each measured fault becomes a fix of its own in the next pass.
3. Two exceptions never hold it: a brief line the footage does not contain, and a caption a person retexted.
4. Before any render the free checks run first (the preflight), so most of them are fixed at $0.

## The review loop
<!-- monti: target=top.loop words=loop,agent loop,re-cut,recut,fix,automatic,iterations,passes,guard,βρόχος,loop,επαναλήψεις,διορθώνει μόνο του -->
The loop renders, reviews, turns the issues into timed fixes, renders again and reviews again, at most 3 reviews.
1. It stops on a pass, on its budget, on the same fix twice, or when a version scores lower (the better one is kept).
2. A stuck loop tries one re-plan from the reviewer's open notes.
3. The Loop button (top bar) shows every version side by side: frame, score, the worst notes, and what made each next version.

## Loop view
<!-- monti: target=top.loop words=loop view,loop film,watch the loop,side by side,compare versions,history of fixes,βρόχου,δες τον βρόχο -->
Loop (top bar) lays every version out left to right: its frame, blind score and change, the reviewer's worst notes (ticked when a later version fixed them), and between two versions what made the second one, with a Compare button. "Every version / Agent only" filters it.

## Hook A/B
<!-- monti: target=insp words=hook,hook a/b,opening,first line,another hook,try another hook,a/b,άνοιγμα,πρώτη ατάκα,αρχή,hook -->
"Try another hook" (in the review inspector) makes a version that opens on another line, reviews it blind, and a pairwise judge watches both twice in swapped order; the new opener is kept only if it wins both (~$0.04 to $0.07).
The editor then opens the version it kept.

## Export
<!-- monti: target=top.export words=export,download,save,file,mp4,render out,εξαγωγή,κατέβασ,αποθήκευσ,export,αρχείο -->
Export (top right) opens the Export page: cover, destination, what goes in the bundle, the pre-flight checks, then Export.
1. Destination: 1080x1920 as rendered, Feed 4:5, Square 1:1, or a 720 copy for messages.
2. Include: the video, the video without captions, SRT, WebVTT, the cover and its grid crop, WAV, a transcript, post text (more than one file comes as a zip).
3. The pre-flight checks the review and gate, −14 LUFS, the format, the length against Reels / TikTok / Shorts and the safe areas.
Exports are $0; Feed and Square render again from a copy.

## Feed 4:5 and Square 1:1
<!-- monti: target=export.dest words=4:5,1:1,export 4:5,export 1:1,square,feed,instagram feed,linkedin,aspect,landscape,τετράγωνο,feed,αναλογία -->
On the Export page pick Feed 4:5 or Square 1:1 under Destination: the version is rendered again in that frame, still following the head, with captions kept clear of the face ($0, about 12 to 30 s).

## Cover
<!-- monti: target=export.cover words=cover,thumbnail,poster,headline,cover image,εξώφυλλο,thumbnail,μικρογραφία -->
On the Export page the cover maker picks a frame and lays a headline over it, shown with the Instagram grid's 3:4 crop.
1. Scrub to a frame, or "Let Muse Spark pick" (three moments with a reason, ~$0.018).
2. Templates: frame, headline, sticker, cutout (the headline behind the speaker, cut out by SAM 3.1, $0.010).
3. "Design it with Muse Spark" picks the still, writes the headline, checks the cover and its tile, fixes it once (~$0.011).

## Post text
<!-- monti: target=export.post words=post text,caption for post,hashtags,description,title for instagram,περιγραφή,hashtags,κείμενο ανάρτησης -->
On the Export page, Post text writes a title, description and hashtags for Reels, TikTok, Shorts and LinkedIn (one Muse Spark call, ~$0.01); Copy, or include post.txt in the bundle.

## Share a link
<!-- monti: target=top.share words=share,link,send,read-only,show someone,κοινοποίησ,link,στείλε,μοιράσ -->
Share (top bar) makes a read-only page for this version: the cut, its score and score history, the top notes; the link is copied. Unshare removes it.

## Cost and time
<!-- monti: target=top.more words=cost,costs,price,how much,money,dollars,spend,ledger,cost and time,κόστος,κοστ,κοστίζει,πόσο κάνει,λεφτά,χρεών -->
More (top bar) > Cost and time shows this project's ledger: every model call with its tokens, dollars and seconds.
1. Rough prices: a new run about $0.045 + $0.0026 per footage second; the review loop about $0.05; a blind review $0.035; an Ask Montivo message about $0.006; a re-plan $0.05 (Spark Open $0.012); a title card or B-roll still $0.01; a look from words $0.008.
2. $0: every edit (trim, delete, move, split, captions, style, music, speakers), Live drafts, exports, renders.
3. A Monti question is about $0.013 (Muse Spark 1.3 reads this whole guide); without the key Monti answers from this guide's keywords for $0.

## Replay
<!-- monti: target=top.more words=replay,watch the run,playback the agent,record,επανάληψη,replay -->
More (top bar) > Replay the agent run plays a finished run back at 1x or 4x, with the steps and the dollars counting up.

## Hosted demo rules
<!-- monti: target=home.budget words=hosted,demo,judge,judges,budget left,limit,password,copy,sample,κριτ,όριο,προϋπολογισμός,demo -->
On the hosted demo everyone shares a model budget; the meter on the project list and the composer show what is left.
1. Editing a sample makes your own copy; other judges never see it.
2. A clip can be up to 90 s and 1000 MB; one job runs at a time on the server.
3. Past the budget, the finished projects and every $0 edit still work; paid calls (a new run, reviews, Ask Montivo, Monti's model) stop, and Monti answers from this guide.

## When a job is running
<!-- monti: target=top.more words=job,running,progress,waiting,slow,stuck,stop,cancel,τρέχει,περιμένω,πρόοδος,σταμάτα,ακύρωσ -->
A running job shows its step, a clock against the expected time and a bar in the top bar, the chat's tray and its Home tile.
1. Stop is in the chat's tray (Assistant panel); a stopped job charges nothing past the calls already made.
2. You can close the tab: the job keeps running and the tab title shows a done mark.
3. A message typed into Ask Montivo during a job waits and goes when it ends.

## Monti
<!-- monti: target=none words=monti,gecko,help,who are you,ποιος είσαι,βοήθεια,σαύρα -->
I'm Monti: I know every corner of Montivo and point to it. Ask "where is ..." or "how do I ..."; Show me runs to the place on screen. For edits to your cut, Ask Montivo (the chat in the Assistant panel) does them.
