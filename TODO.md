#TODOs

### Stage 1:

Get the app to a Element hosted stage that can securely be used by CT in POC mode.

- [ ] RL - Create docker / Kubernetes deployment
  - [ ] Deploy backend scripts
  - [ ] Deploy web UI
- [x] AG - Create a web UI to upload input doc, initiate processing and download output.
- [x] RL - Update LLM from Anthropic -> Cloud hosted (Azure endpoint)
- [x] RL - Some sort of auth to prevent access from the public internet?
  - [x] RL - Hard-code some auth credentials for now
        ~~- [ ] KeyCloak auth later~~
- [x] ~~Replace / implement Brave Search (What is the best tool that we can use for this)~~
  - Can we use the current Brave search API / free tier for now? -- yes
  - Are we near the free usage tier limits at the moment? -- no
    - It appears that we're not near the limit at the moment (AG) / March 9th
      - 194 requests so far this month / two tests (sounds like we get 2000 / month, so 20 processes per month)
  - [x] Use existing free tier access for now
  - [x] AG to check into costs and to share API key with RL
- [ ] AG - Testing once deployment is live in kubernetes cluster
