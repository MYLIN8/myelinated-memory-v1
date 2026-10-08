# How I got here: designing a memory system from neuron myelination

This is the working-out behind **Myelinated Memory**, written for someone who has never read
code and does not need to. It is the order the ideas actually arrived in, including the parts that
turned out to be wrong. Nothing here needs any technical background — if a term is unavoidable, it
gets explained the first time it appears.

The short version: I copied a trick the brain uses to decide **what deserves attention**, then spent
a long time measuring whether the trick actually worked, and publishing the answer even when it was
"no".

---

## 1. The problem I started from

An AI assistant has a memory limit that works a bit like a desk. It can only have so many papers
spread out in front of it at once. Everything it has ever been told is filed away somewhere, but
only what is on the desk can influence the answer it gives you right now.

So there are only two real decisions, and everything else follows from them:

1. **Which papers go on the desk?**
2. **How big a piece of each one do you put there?**

Most systems answer question 1 and stop. They rank everything by how similar it is to your question
and take the top few. That is reasonable, and it works. But it ignores something a person does
instinctively: you don't treat all your memories the same. You don't need to be reminded that your
own name is yours, and you don't keep reciting the phone number of a plumber you called once.

That gap — *similarity is not importance* — is where the biology came in.

## 2. The biology that started it

Neurons pass signals along connections. When a connection gets used a lot, the body wraps it in a
fatty sheath called **myelin**. Myelin is insulation, and insulated wires carry signals faster. So a
pathway you use often becomes a fast pathway; a pathway you stop using loses its insulation and
gets slower, and eventually isn't used at all.

That single mechanism does something remarkable: **the brain lets traffic decide which roads get
paved.** Nobody sits down and ranks the roads. Use is the vote.

The second thing I borrowed is that the brain doesn't only change *whether* it remembers something.
It changes *how much detail* comes back. You recall a familiar idea as a summary and an unfamiliar
one needs the full story. And its memory does this in bulk, at a boundary — when you sleep — rather
than continuously.

I wanted those three ideas and nothing else: **used things get stronger, unused things weaken,
and strength decides how much detail is worth spending.**

## 3. Translating it, one decision at a time

Each step below is one thing I decided, and the reason I decided it that way.

**Step 1 — give every memory a strength number.** A memory starts at a middling strength. Every time
it is actually used, it goes up. Cap it at a maximum, because a memory that has been used a hundred
times is not more than a hundred times as useful. *Biology: a pathway gets paved, not super-paved.*

**Step 2 — let it fade when it is ignored, but not at one speed.** A fading rate that is the same
for everything is wrong in an obvious way: your home address and a lunch order should not expire
together. So each memory carries a **category**, and the category decides how fast it fades —
identity almost never, a passing errand quickly. *Biology: some pathways are reflexes and never
need conscious reinforcement; others are one-off and get pruned.*

**Step 3 — some things must never fade.** A small number of memories are safety-critical or define
who you are. Those get **pinned**: full strength, exempt from fading and from being thrown away.
*Biology: you do not forget how to breathe because you had a busy week.*

**Step 4 — strength decides how much text you get, not just whether you appear.** This is the part
most systems skip. A strong memory arrives in full; a middling one arrives as a summary; a weak one
arrives as a single line. The weak ones still get a chance to be seen, at a price you can afford.
*Biology: a vague sense that something exists, without the detail.*

**Step 5 — spend the desk evenly.** Now the real problem surfaces: given a fixed desk, which
combination of papers and sizes is best? Each memory offers three sizes, each size costs a different
amount of room, and each is worth different amounts. This is the same puzzle as packing a bag, and
the answer is a simple rule: rank the options by *value per character* and take the best ones that
fit. A short summary of something useful can beat the full text of something marginal.
*Biology: recall is lossy, and the loss is chosen by usefulness.*

**Step 6 — do the bookkeeping in one pass, at a natural boundary.** Fading every memory every time
you do anything would be slow and would make the result depend on how often you happened to look.
So the bulk work — age everything, tidy duplicates — happens once when a session begins.
*Biology: consolidation happens at the boundary between waking and sleeping, not in the middle of a
conversation.*

**Step 7 — when a fact changes, say so.** If a fact is replaced, the old version should stop
surfacing, not compete with the new one. So a memory can be **retired**, and a new one can be
flagged as replacing an old one. *Biology: relearning overwrites; it does not stack two versions.*

**Step 8 — two ways of saying the same thing is one memory.** Otherwise the desk fills with
paraphrases of one fact. If a new memory is nearly identical to an existing one, they merge.
Merging is a write-time tidy-up, so the store doesn't quietly bloat. *Biology: you don't hold two
separate recollections of the same afternoon.*

Put together, those eight decisions are the whole system: one number per memory, a fading rule that
depends on what the memory is, a floor that can never fade, a ceiling on how fast it can grow, three
sizes, a packing rule, a tidy-up boundary, and an explicit way to let a fact be replaced.

## 4. Then the hard part: checking whether it was actually true

Here is the part I did not expect. Building it took a fraction of the time; **proving it was any
good took the rest, and several of my beliefs did not survive.**

The temptation with a design like this is to argue from the metaphor: it is inspired by the brain,
so obviously it helps. That is not evidence. So the system had to be compared against the plain,
unromantic alternatives on the same questions:

- A **flat list** that just remembers everything, ignoring importance entirely.
- A **recency list** that keeps whatever was mentioned last.
- A **keyword index** (the classic search-engine ranking, called BM25).
- A **statistical similarity** index that compares the wording of your question with each memory.

And the results, in the order I learned them:

**Finding 1: the size of the answer wins, not the cleverness of the ranking.** My system's big, clear
win is context economy — it puts the same useful fact on the desk using noticeably less room than a
flat list. But when I asked exactly *what* was doing the winning, the answer was embarrassing in a
useful way: someone else's plain keyword ranking, using only my packing rule, matched or beat my
whole system. The **allocator** — the packing rule from Step 5 — did the work. The strength-and-fading
machinery, the part I was proudest of, contributed nothing measurable to that result.

**Finding 2: my strength number was hurting the ranking.** The design added strength to similarity
when ordering memories, so a strong memory could outrank a better-but-unused match. When I finally
measured that weighting, it was a straight cost: at the value I had chosen by hand, the system was
missing facts it would otherwise have found. Turning it down to zero removed the entire gap to the
best competitor. Two lessons, both worth more than the feature: **a hand-picked number deserves
suspicion more than a clever formula**, and **importance should decide how much room a memory gets,
not who wins the argument about what your question is about.** That is now how the system ships.

**Finding 3: the tool I was using to check myself was broken.** The program that was supposed to
measure that weighting had never once run — it crashed immediately, every time, and because its
results looked like "still to be re-run" in my notes rather than an error, I had been quoting an old
measurement for weeks. Fixing the ruler, not the thing being measured, was the single most valuable
hour in this project.

**Finding 4: I was grading the wrong part.** The system reorders memories when it packs them into the
budget, and the quality score I was using was being computed *after* that reshuffling. So it was
grading my packing as if it were my search skill, and the two columns that should have differed were
suspiciously identical. Once the search order was recorded properly, the real numbers appeared —
better than the distorted ones, and, importantly, *trustworthy*.

**Finding 5: the claim I named the project after is still unproven.** The idea that fading alone
would retire an outdated fact is the most beautiful part of the metaphor, and I cannot yet show it
works: with no explicit "this replaced that" signal, the system still surfaces the stale fact. With
an explicit signal it behaves perfectly — but so would any system that simply deletes. That is
recorded as an open lead, not a result.

## 5. What the whole exercise taught me

**The metaphor is a design generator, not a proof.** Biology gave me a shape worth building — used
things get stronger, unused things fade, strength buys detail, tidy up at a boundary. Every one of
those is a real, useful engineering decision. But the moment I wanted to say "and therefore it is
better", biology had nothing to offer; only measurement did, and measurement contradicted me three
times.

**Honest failure is the most valuable output.** A system that reports its own losses tells you where
to look next. My own report says, in its own headline, that a plain keyword index still orders
evidence slightly better than mine, and that the machinery I am proudest of is not what earns the
win. That is not a weakness of the project. It is the only reason the rest of its numbers are worth
believing.

**Where it stands now, in one breath.** The system matches the best traditional ranking on *finding*
the right memory while spending less of its memory budget to do it, and beats a flat or recency-based
store by a wide margin on both. It still comes second on *ordering* — putting the best fact first —
and it is behind on one difficult public test made of long, chatty conversations. The idea that
fading alone retires old facts remains unproven. And the piece that does the most work turned out to
be the least romantic one: a rule about spending a budget well.

If you take one thing from this file, take that last sentence. I set out to build a memory that
behaves like a brain, and what I ended up proving is something simpler and sturdier: **the value is
in choosing what not to spend.**
