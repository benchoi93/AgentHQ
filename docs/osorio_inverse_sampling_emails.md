# Email Exchanges with Carolina Osorio — Inverse Sampling Idea

Extracted from Gmail on 2026-03-28. Emails ordered chronologically.

---

## 1. Follow-up on our conversation yesterday

- **Date:** Fri, 16 Jun 2023
- **From:** Seongjin Choi <chois@umn.edu> (then seongjin.choi@mcgill.ca)
- **To:** Carolina Osorio <carolina.osorio@hec.ca>
- **Subject:** Follow-up on our conversation yesterday
- **Attachments:** 2023_IEEEITSC_hybridSim.pdf, sjchoi_trajgail.pdf, simulation_related.pptx (SharePoint link), nf_idea.pptx (SharePoint link)

> Dear Professor Carolina Osorio,
>
> It was such a pleasure to have wonderful conversation with you yesterday!
> Our discussion on various subjects was truly engaging and I thoroughly enjoyed every moment of it.
> I would like to follow-up on our conversation yesterday and share more about my background and interests.
>
> I'm really interested in data-driven models for predicting/estimating traffic state, especially in probabilistic manner.
> What further excites me is exploring the potential applications of these predicted distribution of traffic state in decision-making processes.
>
> In reflecting on our conversation, I think this is the point where we could engage in a deeper discussion.
> Given your remarkable expertise in optimization and my passion for data-driven modeling, I see something unique from here :D.
> I would love to take this as a long-term collaboration and to learn from your expertise to create something unique and novel.
>
> I would be happy to have another meeting with you sooner or later to explore various research interests we have in common.
>
> Here are some materials you might be interested in:
>
> 1. My experience on developing traffic simulator
>    - Some example videos of traffic simulators I developed 😃 — simulation_related.pptx (SharePoint)
>    - Attached file (2023_IEEEITSC_hybridSim.pdf): manuscript submitted to ITSC by one of my collegue (Yeeun Kim). I designed the overall structure and all the backend structure, and developed microscopic and mesoscopic simulation models. Yeeun developed the "dynamic" part which dynamically decides which part to simulate with microscopic simulator and which part to simulate with mesoscopic simulator.
>    - Google scholar page of my PhD supervisor (Professor Hwasoo Yeo)
>
> 2. My studies on deep generative models
>    - Attached file (sjchoi_trajgail.pdf): this was part of my Ph.D. dissertation. I developed a model based on GAIL (one of the variants of GAN) to generate synthetic urban trajectories (which contains OD choice + route choice). Especially, I like the last two paragraphs I wrote in the conclusion where I discussed the similarities and differences between my proposed model and discrete choice modeling and dynamic traffic assignment.
>    - Some preliminary ideas I have right now for using Flow-based Generative Models (a neural network model that can directly estimate the likelihood of the unknown distribution) for transportation applications — nf_idea.pptx (SharePoint)
>
> 3. Yesterday you mentioned that it would be great if data-driven models can recommend which scenarios to simulate next either 1) for better performance or 2) for better exploration.
>    and actually one of my colleague (Jinwon Yoon) kind of tackled the similar problem with (2 - for better exploration) in his Ph.D. dissertation.
>    He was working on Reinforcement Learning for traffic signal control and found that when we use traffic simulator, the exploration is very restricted compared to other RL baseline environment (CartPole and Pendulum)

---

## 2. LLMs for Simulation-based Optimization?

### 2a. Choi → Osorio

- **Date:** Fri, 3 Nov 2023
- **From:** Seongjin Choi <chois@umn.edu>
- **To:** Carolina Osorio <carolina.osorio@hec.ca>
- **Subject:** LLMs for Simulation-based Optimization?

> Hello Professor Osorio,
>
> I just realized that I never got back to you after our last meeting.
>
> I was studying more on Bayesian optimization and simulation-based optimization which we discussed during our last meeting.
>
> and then somehow I came across this paper called "Large Language Models as Optimizers"
> and I thought like... can we use LLMs to find the best next simulation scenario by describing our input and the current score (performance) in natural language?
>
> and I thought can LLMs find better (in terms of average iteration of finding optimal solution?) exploration algorithms for signal control or other simulation-based optimization problems?
>
> Maybe this can be something interesting to investigate.
>
> As a side note, one of the things they did in this "LLMs as Optimizer" paper was that they optimized the 'prompts' that make LLMs can solve complex problems.
> average performance without this type of prompting was around 34.
> average performance of hand-crafted prompts (from humans) was around 71.8.
> and surprisingly the average performance of the prompt LLM found was 80.2
>
> and the prompt it found (80.2 scoring) was "Take a deep breath and work on this problem step-by-step"
> I thought this was funny 😂😂 because it looks like some sort of psychology for LLM.
>
> Do you have any thoughts on this?

### 2b. Osorio → Choi

- **Date:** Thu, 30 Nov 2023
- **From:** Carolina Osorio <carolina.osorio@hec.ca>
- **To:** Seongjin Choi <chois@umn.edu>
- **Subject:** Re: LLMs for Simulation-based Optimization?

> Hi S.,
>
> I am not super excited abt getting on the LLM train, mainly bc to do impactful work you need *a lot of data* that we don't have access to in academia.
>
> What are your thoughts on the inverse sampling w/ a linear matrix approach that we discussed? If you're interested, I can put together a pseudo-code, likely mid-Dec. or so, and you can play around with it to see if there's potential for nice contributions there. wdyt?
>
> C.

### 2c. Choi → Osorio

- **Date:** Tue, 5 Dec 2023
- **From:** Seongjin Choi <chois@umn.edu>
- **To:** Carolina Osorio <carolina.osorio@hec.ca>
- **Subject:** Re: LLMs for Simulation-based Optimization?

> Hello Professor Osorio,
>
> I was too occupied with moving lately. Sorry for the late reply.
>
> yes! I'm definitely interested in the linear matrix approach that we discussed.
> If you can put together a pseudo-code, I will study it with the paper you sent previously.
>
> I'm in Montreal until the end of December. I leave on Jan 1st. if you have some time, let's have some coffee before I leave :D.
> Btw, are you coming to TRB?

---

## 3. Starter code (finally!)

- **Date:** Fri, 8 Mar 2024
- **From:** Carolina Osorio <carolina.osorio@hec.ca>
- **To:** Seongjin Choi <chois@umn.edu>
- **Subject:** Starter code (finally!)
- **Attachments:** ForSeongjin_march2024.zip, paper_R1_TS_tayOsorio.pdf

> Hi S.,
>
> Great to hear that you're surviving the first term. In my experience, the first year on the faculty is the most brutal ! Once you survive that, it's downhill from there 🙂
>
> I finally found time to draft an (incomplete) pseudo-code for our idea.
>
> As a reminder, the goal is to extend Timothy's inverse-cdf sampling method, attached is that paper, which will be available soon on Transportation Science.
>
> The extension is based on using an assignment matrix (i.e., linear network loading map). This would allow the method to be used in a more generic way for a variety of TR (Transportation) problems. Basically, it could be used for a variety of problems where one would like to sample a vector (e.g., an OD, a signal plan, a congestion pricing policy, etc) that yields a given distribution of road (i.e., segment) demands. In other words, the vector (eg ODs, signal plans, congestion prices) can be sampled such as to yield a specific spatial distribution of congestion.
>
> Attached are hand-written notes on the main idea, and a starter python notebook that is not yet complete, but has the full flow.
>
> Have a look at it, and we can schedule a time to discuss the goals/approach.
>
> Thanks for being patient w/ me on this one!
> C.

---

## 4. NeurIPS paper (thread includes sampling status updates)

### 4a. Osorio → Choi

- **Date:** Wed, 15 Oct 2025
- **From:** Carolina Osorio <carolina.osorio@hec.ca>
- **To:** Seongjin Choi <chois@umn.edu>
- **Subject:** NeurIPS paper

> Hi S.,
>
> Any chance there's a shareable version of our NeurIPS paper that I can share? I have had a couple of folks asking for it.
>
> Regarding our sampling work, I have not had any cycles to advance the code. I will keep you posted once I can make progress on the coding.
>
> Thanks!
> C.

### 4b. Choi → Osorio

- **Date:** Tue, 21 Oct 2025
- **From:** Seongjin Choi <chois@umn.edu>
- **To:** Carolina Osorio <carolina.osorio@hec.ca>
- **Subject:** Re: NeurIPS paper
- **Attachments:** view.pdf

> Hello Carolina,
>
> Sorry for the late reply!!! Things have been chaotic for me recently because of the newborn.
>
> We have finalized the edits, and I just submitted the preprint to arXiv. The GitHub repo should be almost ready, too. We're just finalizing a few things that we promised during rebuttal.
> We'll upload the camera-ready version on the NeurIPS webpage. (probably tomorrow)
>
> btw, are you coming to San Diego?

### 4c. Osorio → Choi

- **Date:** Sun, 26 Oct 2025
- **From:** Carolina Osorio <carolina.osorio@hec.ca>
- **To:** Seongjin Choi <chois@umn.edu>
- **Subject:** Re: NeurIPS paper

> Hola, hola,
>
> How is the paternity leave going?
> Not planning to go to San Diego this time around. But pls do keep me posted if there are new opportunities that arise based on our BO4Mob work. Btw any chance you have a PhD student interested in taking up our sampling work? I have not managed to identify a strong candidate to revive that work, and would love for us to continue working on it.
>
> All the best for an enjoyful paternity leave!
> C.

### 4d. Choi → Osorio

- **Date:** Wed, 29 Oct 2025
- **From:** Seongjin Choi <chois@umn.edu>
- **To:** Carolina Osorio <carolina.osorio@hec.ca>
- **Subject:** Re: NeurIPS paper

> Hi Carolina,
>
> Things are going well! just less sleep these days :D
>
> I talked about this with students during the summer, and Seunghee (one of the first authors of BO4Mob) showed some interest.
> But she's going back to Korea in January, and I'm not sure if she could continue working on it. I can discuss this further with her and get back to you!

### 4e. Osorio → Choi

- **Date:** Wed, 29 Oct 2025
- **From:** Carolina Osorio <carolina.osorio@hec.ca>
- **To:** Seongjin Choi <chois@umn.edu>
- **Subject:** Re: NeurIPS paper

> Sounds good, S. I'll also keep an eye out for talented collaborators that could help us out.
>
> C.

---

## 5. Reviving the Sampling Idea

### 5a. Osorio → Choi

- **Date:** Fri, 20 Feb 2026
- **From:** Carolina Osorio <carolina.osorio@hec.ca>
- **To:** Seongjin Choi <chois@umn.edu>
- **Subject:** Re: [TRN] Postdoc opening at University of Minnesota (AI for transportation)

> I'll make sure to spread the news.
> We dropped the ball on our sampling idea, would be nice to find a student to revive it. Let me see on my end if I can find someone with bandwidth.
>
> C.

### 5b. Choi → Osorio

- **Date:** Wed, 25 Feb 2026
- **From:** Seongjin Choi <chois@umn.edu>
- **To:** Carolina Osorio <carolina.osorio@hec.ca>
- **Subject:** Re: [TRN] Postdoc opening at University of Minnesota (AI for transportation)

> Hi Carolina,
>
> Yes, we should definitely revive it.
> I've been quite busy lately with my newborn (now five months old!) so I haven't had a chance to work on this idea.
> But since we already have the testbed and setups in place, I think it would be great to get it going again.
>
> I'll try to identify a good student who could work on this too

---

## Related: BO4Mob Collaboration Emails (not directly about inverse sampling)

### NeurIPS Submission Announcement

- **Date:** Fri, 16 May 2025
- **From:** Seongjin Choi <chois@umn.edu>
- **To:** Donghoon Kwon, Seunghee Ryu, Aryan Deshwal, Seungmo Kang, Carolina Osorio
- **Subject:** Neurips submission
- **Attachments:** BOBenchmark_NeurIPS_Benchmark (9).pdf

> Dear all,
>
> We have finalized the NeurIPS submission! The final submitted manuscript is attached (also you can find it from the openreview page)
>
> Unfortunately, we couldn't get a reasonably good result for the Full Region, so we excluded it from the main part but we provided some results in the appendix.
>
> We also migrated the code to the following link. if you'd like to get read/write access, please let me know
> https://github.com/UMN-Choi-Lab/BO4Mob
>
> We're planning to submit the same paper with more emphasis on transportation to TRBAM 2026 (Transportation Research Board Annual Meeting 2026)
> if we get rejected from NeurIPS, we can further strengthen it and submit it to other venues that accept benchmark papers like ICLR

### SPSA Code Bug Discussion

- **Date:** Sun, 10 Aug 2025
- **From:** Seongjin Choi <chois@umn.edu>
- **To:** Carolina Osorio <carolina.osorio@hec.ca>
- **Subject:** Re: spsa code
- **Attachments:** 5x image.png (code screenshots)

> (Osorio flagged a potential bug in the SPSA optimizer code at https://github.com/UMN-Choi-Lab/BO4Mob/blob/main/src/optimizers/spsa.py — the `spsa_update` function never uses the input parameter `f`. Choi explained that `spsa_update` is only used to get `d_plus` and `d_minus`, and the actual function evaluations happen separately in lines 138-170.)

---

## Key Attachments to Locate

| Date | Attachment | Description |
|------|-----------|-------------|
| Jun 2023 | 2023_IEEEITSC_hybridSim.pdf | Hybrid simulator paper (Yeeun Kim) |
| Jun 2023 | sjchoi_trajgail.pdf | TrajGAIL paper (Choi's PhD work) |
| Mar 2024 | **ForSeongjin_march2024.zip** | **Hand-written notes + starter Python notebook for the inverse sampling idea** |
| Mar 2024 | **paper_R1_TS_tayOsorio.pdf** | **Tay & Osorio paper (Transportation Science R1) — the base method being extended** |
| Oct 2025 | view.pdf | NeurIPS camera-ready or preprint |
| May 2025 | BOBenchmark_NeurIPS_Benchmark (9).pdf | BO4Mob NeurIPS submission |
