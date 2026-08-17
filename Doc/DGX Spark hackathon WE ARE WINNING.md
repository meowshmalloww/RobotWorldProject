Only building the UI/UX Frontend first, we will use [https://signoz.io/](https://signoz.io/) and [https://github.com/SigNoz/signoz](https://github.com/SigNoz/signoz) for the dashboard and whatever; we will use ​Bright Data   
Scraper Studio to scrape data and whatever. [Part.](http://Part.IO)io I will use later, but currently I cannot use it. No Fake Toy Simulation Demo, must be final production and must be built without any issues, problems, or bugs. Research and search, and think before implementing. We will be using gpt luna or gpt terra and openai api key for models. We will use Microsoft Trillium 2\. It is basically like a game engine for training. I prefer you check all NVIDIA papers and videos and everything and research, no gaussian splatting for now. We will use python and React TS JS and Vulkan we are making software.

**An autonomous world-building and curriculum engine for physical AI.**

It watches a robot fail, determines what skill/environment is missing, finds real examples online, reconstructs those objects/environments with real physical properties, creates interactive simulation worlds, trains/tests the robot, measures improvement, and repeats.

**Training is not the product. Building the exact worlds the robot needs is the product.**

Flow might look something like this: User:  
"Teach this robot to open refrigerators"  
              ↓  
Port: Skill \= Open Refrigerator  
Current success \= 20%  
              ↓  
Run robot in simulation  
              ↓  
        Robot fails  
              ↓  
SigNoz captures everything  
 actions / collisions / joint state /  
 timing / errors / success / traces  
              ↓  
Agent queries SigNoz  
              ↓  
Diagnoses:  
"Robot fails on horizontal handles  
 and heavy left-hinged doors"  
              ↓  
Agent queries Port  
              ↓  
Current coverage:  
3 refrigerator types  
Missing:  
heavy doors \+ horizontal handles  
              ↓  
Bright Data  
              ↓  
Google Images / Lens / Web Search  
              ↓  
Find exact refrigerators  
              ↓  
Scraper Studio  
              ↓  
Images \+ dimensions \+ weight \+  
materials \+ manuals \+ part information  
              ↓  
3D / PHYSICS WORLD BUILDER  
              ↓  
Generate geometry  
Split moving parts  
Scale correctly  
Add joints  
Add collisions  
Add mass/material  
Build OpenUSD  
              ↓  
Create 10 targeted variations  
              ↓  
Train / adapt robot  
              ↓  
Evaluate again  
              ↓  
Success 20% → 76%  
              ↓  
Agent identifies next weakness  
              ↺

Example  
Its SERP API currently supports **Google Images** and even **Google Lens / visual matches / exact matches**. Google Images results can be returned as parsed JSON.

So don't do:

> randomly scrape Google HTML.

Do this:

### **Step 1 — discover the exact object**

Agent searches:

"Samsung RF28T5001SR refrigerator"

Bright Data Google Search finds manufacturer/product pages.

### **Step 2 — image search**

Bright Data Google Images:

Samsung RF28T5001SR front side open

Request large images.

### **Step 3 — verify identity with Lens**

Take the best candidate image → Bright Data Google Lens → exact/visual matches.

That verifies that:

image A  
image B  
manual  
manufacturer page  
retailer page

all refer to the **same SKU/model**, rather than accidentally mixing three refrigerators. Bright Data explicitly supports Lens visual/exact-match queries.

### **Step 4 — Scraper Studio extracts the real data**

This is the mandatory hackathon part.

Scrape:

{  
 "manufacturer": "Samsung",  
 "model": "RF28T5001SR",

 "dimensions": {  
   "width": "...",  
   "height": "...",  
   "depth": "..."  
 },

 "weight": "...",  
 "materials": \["..."\],

 "images": {  
   "front": "...",  
   "open": "...",  
   "side": "..."  
 },

 "manual\_url": "...",

 "features": \[  
   "two upper doors",  
   "lower freezer drawer"  
 \]  
}

Scraper Studio's CLI creates a stable Collector ID and can repair the same scraper when the source HTML changes. The required run → heal → approve → rerun lifecycle is already built into the CLI.

And Bright Data's CLI uses its Web Unlocker and Browser API infrastructure underneath, so it's much better positioned for anti-bot pages than a basic Firecrawl-style fetcher. It's still not magically 100% successful on every website, so use manufacturer pages plus fallback sources.

---

# **Don't scrape “gravity”**

One correction.

Gravity is a **world property**:

Earth gravity ≈ 9.81 m/s²

You don't need refrigerator-specific gravity.

You need to scrape or infer:

dimensions  
mass  
material  
center of mass  
density  
friction  
moving parts  
joint type  
joint position  
joint limits

Some of these won't be available online.

So every property should have:

{  
 "value": 18.2,  
 "source": "manufacturer\_manual",  
 "confidence": 1.0  
}

or:

{  
 "value": 0.35,  
 "source": "material\_prior",  
 "confidence": 0.62  
}

Then uncertain properties get **domain-randomized**:

friction \= 0.25 – 0.45  
door resistance \= 8 – 14 N

That's actually better training than pretending an inferred number is exact.

---

# **The hardest problem: making TRELLIS objects interactive**

You identified the biggest technical problem correctly.

TRELLIS.2 generates a **static textured mesh**. It doesn't magically know:

this is the body  
this is door \#1  
this is door \#2  
this is a drawer  
this is the handle  
this hinge rotates 110°

TRELLIS.2 outputs high-quality PBR GLB geometry, but articulation is something **we need to build afterward**.

## **Solve it with three asset types**

### **Type A — rigid object**

Bottle, trash bag, bowl, box.

Easy:

image  
→ TRELLIS  
→ scale  
→ mass  
→ collider  
→ USD

### **Type B — articulated object**

Refrigerator, cabinet, oven, door.

Use:

Bright Data  
      ↓  
images \+ manual \+ specs  
      ↓  
VLM produces PART GRAPH  
      ↓  
body  
├── left\_door  
│   └── handle  
├── right\_door  
│   └── handle  
└── freezer\_drawer  
      ↓  
generate/separate geometry  
      ↓  
assemble in OpenUSD

Example:

left\_door:  
   joint: revolute  
   axis: Y  
   range: 0° → 110°

freezer\_drawer:  
   joint: prismatic  
   axis: Z  
   range: 0 → 0.55m

OpenUSD already has rigid bodies, mass, collision geometry, revolute joints, prismatic joints, limits, and articulations. That's exactly why I recommend it instead of treating GLB as your final format.

### **Type C — environment**

Room/factory/kitchen.

Use Gaussian reconstruction for visual appearance plus normal mesh geometry for physics.

# **What OpenUSD actually is**

Think:

**GLB \= primarily the object's geometry/material appearance.**

**OpenUSD \= the entire simulation description.**

It can represent:

Kitchen  
├── Floor  
├── Refrigerator  
│   ├── Body  
│   ├── DoorLeft  
│   │    └── RevoluteJoint  
│   ├── DoorRight  
│   └── Drawer  
├── Table  
├── Bowl  
└── Robot

along with:

transforms  
scale  
materials  
mass  
colliders  
joints  
joint limits  
semantics  
physics

USD Physics was explicitly designed to represent rigid-body simulation for areas including robotics and AI.

And NVIDIA's current SimReady specifications build simulation metadata—physics, mass, collisions, semantic labels and other properties—on top of OpenUSD.

So:

> **TRELLIS creates appearance. Your compiler creates behavior.**

That's an excellent technical story.

# **Gaussian splatting: YES, but as a second layer**

I would add it **after the object pipeline works**.

Your proposed idea:

> Bright Data finds three/four photos of a room → reconstruct room → put generated objects inside.

This is now surprisingly feasible.

NVIDIA's **InstantSplat** specifically targets sparse-view reconstruction and reports reconstruction from as few as **2–3 images**.

However, the images must depict **the same physical room with overlapping views**.

You cannot use:

random factory photo 1  
random factory photo 2  
random factory photo 3

and expect a coherent reconstruction.

Instead Bright Data should find:

one specific kitchen / factory / room  
├── image 1  
├── image 2  
├── image 3  
└── image 4

A property listing/showroom/gallery with several views is much better.

### **Representation**

Gaussian splat  
   \= photorealistic appearance

hidden/simple mesh  
   \= floor / walls / physical collision

OpenUSD objects  
   \= refrigerator / cabinet / trash / robot

NVIDIA uses essentially this same split: Gaussian PLY for appearance plus GLB collider geometry for physics inside Isaac Sim.

So yes: **Gaussian world \+ physics assets** is excellent.

But Gaussian splatting should be a stretch feature because your 4080 also has to handle everything else.

---

# **SigNoz: make it critical, not decorative**

Use **SigNoz Cloud**, not self-hosted, during the hackathon.

SigNoz Cloud accepts OpenTelemetry directly from your Python services using an ingestion key. Self-hosting on Windows currently requires more setup and SigNoz recommends native Docker inside WSL2 for its Docker deployment, which is unnecessary work here.

Every iteration gets one trace:

curriculum.iteration  
├── robot.evaluate  
├── failure.analyze  
├── port.coverage.query  
├── brightdata.image.search  
├── brightdata.scrape  
├── asset.generate  
├── articulation.build  
├── usd.compile  
├── simulation.start  
├── training.run  
└── robot.evaluate\_again

Metrics:

skill.success\_rate  
robot.collision\_rate  
grasp.success\_rate

scraper.failure\_rate  
scraper.repair\_count

asset.generation\_time  
asset.validation\_failures

training.loss  
training.duration

simulation.fps  
simulation.crashes

### **And here's the important part**

Your agent should **query SigNoz**, not just look at its dashboard.

SigNoz now exposes a Traces API that supports searching and aggregating trace data programmatically.

So:

Agent:  
"Give me failures from the last 20  
refrigerator-opening evaluations."

SigNoz:  
73% failed after handle contact  
21% failed approaching  
6% timeout

Agent:  
"Problem is manipulation after grasp."

Now SigNoz is literally part of the agent's reasoning loop.

Remove SigNoz → the agent loses its main failure-analysis source.

That's excellent sponsor usage.

---

# **Port: this is how we make it equally important**

The Luma requirement only requires Port for goals, technical choices, risks, tasks and the final API/scraper/service catalog. Do all of that first because the judges explicitly ask for it.

But then go further.

Port becomes the agent's **world/skill knowledge catalog**.

Entities:

RobotPolicy  
Skill  
Asset  
Environment  
Scenario  
Scraper  
TrainingRun  
Evaluation  
Service

Example:

SKILL  
Open Refrigerator

success:           54%  
target:            85%

coverage:  
 normal doors     ██████████  
 heavy doors      ███  
 low handles      ██  
 horizontal       ████  
 vertical         ████████

latest weakness:  
heavy horizontal-handle doors

\[GENERATE MISSING WORLDS\]

Port's current platform includes a Context Lake, scorecards, workflows, actions and custom AI-agent management. External agents can consume Port's context and invoke governed actions through its MCP/API.

So your agent can ask:

> “What refrigerator scenarios have already been generated?”

Port answers.

Then it avoids generating duplicate worlds.

Remove Port → the system loses skill coverage, asset history, version/promotion state, and lifecycle control.

**Now all three sponsors are fundamental.**

For the training, keep it real but small I will put 3b VLA or VLM in the Jetson Nano Super Dev Kit don’t worry for now.

# **What should the training actually be?**

Start simple.

The agent identifies:

Open cabinet  
success \= 45%

It produces targeted worlds.

Then a scripted motion planner supplies successful simulated trajectories:

approach handle  
↓  
grasp  
↓  
pull  
↓  
release

Record:

RGB  
robot state  
action  
task instruction

as training examples.

Fine-tune the small policy briefly.

Then rerun evaluation.

Before: 45%  
After:  68%

The **agent chooses what data/worlds to generate**.

That's the interesting part—not the optimizer itself.

---

# **Software stack**

Since you've ruled out C++ and Qt/QML, I would use:

Python 3.11  
├── FastAPI                 orchestration/API  
├── asyncio                 agent jobs  
├── Pydantic                schemas  
├── OpenTelemetry           telemetry  
├── OpenUSD Python          scene/physics authoring  
├── trimesh                 mesh processing  
├── PyTorch                 models/training  
├── LeRobot                 robot policy  
├── Isaac Sim / Isaac Lab   simulation  
├── InstantSplat            optional rooms  
└── TRELLIS.2 / SF3D        objects

### **Vulkan**

Don't write raw Vulkan.

For the native preview, use:

**PyGfx \+ wgpu-py \+ GLFW**

`wgpu-py` can explicitly force the **Vulkan backend**, including on Windows, and PyGfx gives you a Python 3D renderer above it.

So:

Python  
↓  
PyGfx  
↓  
wgpu-py  
↓  
Vulkan  
↓  
RTX 4080

No C++.

For the main polished interface, I'd still use **React/TypeScript** and let the Vulkan viewport be the simulation/asset viewer. Don't spend hackathon time writing UI widgets inside Vulkan.

### **The project in one sentence**

> **An agent autonomously discovers what a robot is bad at, gathers the real-world data needed to recreate that situation, builds an interactive SimReady world, trains/tests the robot, observes the result, and repeats.**

## **1\. Scrape the object AND how it operates**

For each object, Bright Data should try to retrieve several classes of information:

| Data | Example |
| ----- | ----- |
| Images | front, side, product photos |
| Geometry | 31 × 22 × 18 cm |
| Mass | 1.4 kg |
| Material | ABS plastic |
| Parts | body, lid, handle |
| Joint type | revolute hinge |
| Joint axis | vertical / horizontal |
| Joint limits | 0°–110° |
| Operation | pull handle → rotate door |
| Affordances | graspable handle, pushable button |
| State | open/closed, on/off |
| Manuals | manufacturer instructions/spec sheet |
| Source/confidence | manufacturer / retailer / inferred |

This is exactly the type of information a real SimReady object needs. NVIDIA's current SimReady definition includes not just geometry, but **mass, collisions, friction, semantics, articulation limits, actuator properties, and behavioral metadata**.

So instead of:

photo → TRELLIS → GLB

you build:

WEB  
↓  
images \+ manuals \+ specifications  
↓  
3D appearance  
\+  
physical properties  
\+  
articulation  
\+  
affordances  
↓  
INTERACTIVE SimReady OBJECT

That is **much better**.

For example, a cabinet could become:

cabinet.usd

body  
door  
handle

door:  
 joint \= revolute  
 hinge\_side \= left  
 limit \= 0°–110°

mass \= 18.2kg  
material \= wood

affordances:  
 handle \= grasp  
 door \= pull/open

If exact friction/hinge torque isn't published, **don't fake precision**. Mark it as inferred and domain-randomize it during simulation.

# **2\. The agentic loop should be this**

This is where I'd borrow the **good concept from MuscleMemory**, but make it serve your world-building system.

                ROBOT TASK  
                    ↓  
             Run simulation  
                    ↓  
               Did it fail?  
               ↙       ↘  
             NO         YES  
             ↓           ↓  
           PASS     Agent analyzes  
                         ↓  
                "What is it bad at?"  
                         ↓  
               Search existing worlds  
                     in Port  
                         ↓  
               Missing experience?  
                         ↓  
                    Bright Data  
                         ↓  
       Find relevant objects / rooms / manuals  
                         ↓  
               Build new 3D scenarios  
                         ↓  
               SimReady compilation  
                         ↓  
                  Train / adapt  
                         ↓  
                   Re-evaluate  
                         ↓  
                      repeat

Example:

> Robot repeatedly fails to open side-hinged cabinet doors.

Agent determines:

Weakness:  
grasp \+ pull on left-hinged doors

Current training coverage:  
2 doors

Needed:  
different handle heights  
different door widths  
different hinge resistance  
different handle shapes

Then the agent asks Bright Data for relevant examples.

Bright Data retrieves:

10 cabinet products  
images  
dimensions  
door configurations  
materials  
manual/spec information

Your system builds variations.

Then:

scenario 01  
scenario 02  
scenario 03  
...  
scenario 20

Robot trains/tests.

Then the agent evaluates again.

**That is legitimately agentic.**

# **3\. Don't let training become the product**

This is the important boundary with MuscleMemory.

### **MuscleMemory**

world → robot fails → improve ROBOT

### **Yours**

robot fails  
    ↓  
agent discovers missing WORLD coverage  
    ↓  
find real-world information  
    ↓  
construct targeted worlds  
    ↓  
use those worlds to improve robot

So your core contribution remains:

> **Automatically constructing the training data/environment that physical AI needs.**

Training simply **proves why those environments matter**.

That's a stronger project.

---

# **4\. Sponsor integration becomes extremely natural**

The Luma page says the project needs to combine **Port \+ Bright Data Scraper Studio \+ SigNoz into one complete lifecycle**, and specifically allows an automated pipeline **or agentic workflow**.

| Sponsor | What it does in YOUR system |
| ----- | ----- |
| **Port** | The agent's world/asset/training catalog \+ project architecture \+ health/control plane |
| **Bright Data** | Agent's connection to real-world objects, manuals, specifications, environments |
| **SigNoz** | Agent's eyes into what failed and why |

### **Port**

Port contains:

Robots  
Skills  
Assets  
Worlds  
Scrapers  
Training Runs  
Evaluation Runs  
Failures  
Services

Example:

Skill: Open Cabinet

Coverage        41%  
Success         54%

Weaknesses:  
\- left hinge  
\- low handles  
\- heavy doors

World coverage:  
7 / 24 scenario families

Action:  
\[Generate Missing Training Worlds\]

Port actions can be executed by humans **or AI agents**, and Port is designed around catalog-grounded agentic workflows.

This is much more interesting than using Port as a project checklist.

**But:** the hackathon explicitly requires you to set up goals, technical choices, risks and tasks in Port **before coding**, and later catalog your APIs/scrapers/services. Do that too.

---

# **5\. Bright Data becomes incredibly important**

The agent can issue something conceptually like:

Need:  
10 real examples of household cabinet doors

Required information:  
\- clean image  
\- width / height  
\- material  
\- hinge configuration  
\- handle location  
\- relevant operating/manual information

Bright Data finds/extracts the structured information.

And critically, the Luma requirement says you need to demonstrate **automatic scraper repair when HTML changes** and keep scraper settings in your coding-agent rules file.

So during the demo:

Agent needs new object  
↓  
Bright Data scraper runs  
↓  
website changed  
↓  
scraper fails  
↓  
SigNoz sees failure  
↓  
Bright Data repairs scraper  
↓  
data returns  
↓  
world generation continues

That's perfect for this hackathon.

---

# **6\. SigNoz actually fits REALLY well now**

Yes, **SigNoz fits your project extremely well**.

Don't just monitor your FastAPI endpoint.

One agent iteration should be one distributed trace:

agent.analyze\_failure  
├─ signoz.query\_previous\_run  
├─ port.query\_training\_coverage  
├─ agent.plan\_curriculum  
├─ brightdata.search  
├─ brightdata.scrape  
├─ data.validate  
├─ image\_to\_3d.generate  
├─ simready.compile  
├─ physics.validate  
├─ world.compose  
├─ training.run  
├─ robot.evaluate  
└─ port.publish\_result

Track:

robot\_success\_rate  
collision\_rate

scrape\_success\_rate  
missing\_fields  
scraper\_repairs

3d\_generation\_time  
usd\_validation\_failures

simulation\_fps  
simulation\_failures

training\_duration  
evaluation\_success

agent\_iterations  
agent\_retries

And here's something especially useful: SigNoz now has APIs for **programmatically querying traces and metrics**, so your agent can actually inspect previous failures instead of SigNoz being a passive dashboard.

So:

Robot fails  
   ↓  
telemetry → SigNoz  
   ↓  
Agent queries SigNoz  
   ↓  
"72% of failures involve  
low grasp clearance on hinged objects"  
   ↓  
Agent requests new scenarios

**Now SigNoz is part of the intelligence loop.**

That's much better.

# **8\. The final product becomes much better than our earlier version**

Before:

> web data → generate a robot-ready object.

Now:

> **Robot failure → agent understands missing experience → retrieves real-world examples → reconstructs interactive objects and environments → generates targeted training worlds → trains/tests → observes improvement → repeats.**

That is much more compelling.

### **Core loop**

             ┌───────────────────────┐  
             │      ROBOT / VLA      │  
             └───────────┬───────────┘  
                         ↓  
                    EVALUATION  
                         ↓  
                      SigNoz  
                         ↓  
                FAILURE ANALYSIS AGENT  
                         ↓  
                      Port  
                What coverage exists?  
                         ↓  
                   What is missing?  
                         ↓  
                  Bright Data  
                         ↓  
        images \+ specs \+ manuals \+ behavior  
                         ↓  
                WORLD BUILDER  
                         ↓  
 3D \+ physics \+ joints \+ semantics \+ affordances  
                         ↓  
                SimReady / OpenUSD  
                         ↓  
                 TRAIN / TEST  
                         ↓  
                      ROBOT  
                         ↺

### **Project**

Build a **self-healing pipeline that turns web product data into robot-ready 3D simulation assets**.

The flow is:

**Bright Data → 3D generation → SimReady/OpenUSD → robot simulation → validation → Port \+ SigNoz**

### **What the user does**

They enter something like:

> “Create a warehouse robot test for this detergent bottle.”

The system then:

1. **Bright Data** finds/scrapes:  
   * product images  
   * dimensions  
   * weight  
   * material  
   * source/provenance  
2. Generate the 3D object:  
   * **Stable Fast 3D / SPAR3D** for reliable live generation  
   * **TRELLIS.2** optionally for higher-quality remote generation  
3. Convert the raw mesh into a real simulation asset:  
   * correct physical scale  
   * collider  
   * mass  
   * friction/material  
   * semantic label  
   * OpenUSD  
   * validate it as simulation-ready  
4. Put it into a simple **Isaac Sim warehouse/table scene**.  
5. A robot performs a simple task:  
   * pick object  
   * move object  
   * place it in a bin  
6. Record whether the object actually works in simulation.

### **Why Port matters**

**Port is the control plane.**

It shows:

* data sources  
* scrapers  
* generated assets  
* robot scenarios  
* pipeline runs  
* health status  
* validation score

Actions like:

**Build Scenario → Validate → Retry → Promote → Rollback**

Port can show:

> Asset v1 — ACTIVE ✅  
>  Asset v2 — TESTING 🟡  
>  Asset v2 — ROBOT VALIDATION PASSED ✅  
>  Promoted to ACTIVE

### **Why Bright Data matters**

Bright Data is not just downloading random pictures.

It provides the **real-world information required to make the generated object physically meaningful**.

Example:

image  
height \= 27.1 cm  
width \= 12.2 cm  
depth \= 8.6 cm  
mass \= 410 g  
material \= HDPE plastic

Then purposely change the source website HTML.

The scraper breaks.

Bright Data repairs the scraper using the **same Collector ID**.

Then the pipeline continues.

That is one of the most important parts of your hackathon demonstration.

### **Why SigNoz matters**

SigNoz observes the entire pipeline:

scrape  
↓  
validate data  
↓  
generate 3D  
↓  
build collider  
↓  
convert USD  
↓  
load simulation  
↓  
robot test  
↓  
publish

When something breaks:

SCRAPE FAILED 🔴  
missing height  
↓  
Bright Data repair  
↓  
SCRAPE RECOVERED 🟢  
↓  
asset rebuilt  
↓  
robot test passed

So judges can literally see the **zero-downtime recovery**.

---

# **What NOT to build**

Don't try to build:

* arbitrary indoor \+ outdoor worlds  
* your own Vulkan engine  
* Qt/QML application  
* full robot RL training  
* arbitrary robot foundation model  
* live Gaussian-splat reconstruction  
* full world generation from scratch

Those make the project much more likely to fail.

### **Gaussian splatting**

**Optional only.**

You could have:

Gaussian splat \= realistic visual room  
\+  
simple mesh \= floor/walls/collisions  
\+  
generated 3D objects \= robot interaction

But don't make Gaussian splatting required.

### **Robotics**

Use **one robot \+ one reliable task**.

For example:

**Franka arm sorts newly discovered packages into bins.**

Don't train a giant robot model live.

A scripted controller is enough because the purpose is proving:

> **Can the generated asset actually be used by a robot?**

---

# **Best demo**

Start with a product page.

### **Normal run**

Product page  
↓  
Bright Data  
↓  
image \+ dimensions \+ mass  
↓  
3D object  
↓  
OpenUSD \+ physics  
↓  
Isaac Sim  
↓  
robot picks it  
↓  
PASS ✅

Then deliberately change the webpage.

Website redesigned  
↓  
scraper breaks  
↓  
SigNoz detects failure 🔴  
↓  
Port marks source DEGRADED  
↓  
old working simulation stays online  
↓  
Bright Data repairs scraper  
↓  
new asset generated  
↓  
robot tests it  
↓  
PASS  
↓  
Port promotes new version

That's your **grand-prize moment**.

---

## **One-sentence pitch**

> **We turn constantly changing web data into validated, robot-ready 3D simulation assets—and automatically repair, retest, and redeploy them when the real-world source changes.**

Or simpler:

> **A self-healing data-to-simulation pipeline for physical AI.**

